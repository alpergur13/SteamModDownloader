import os
import subprocess
import sys
import requests
import re
import shutil
import time
import concurrent.futures
import threading
import colorama
import psutil
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QLabel, QLineEdit, QTextEdit, QFileDialog,
                             QProgressBar, QTabWidget, QMessageBox, QTableWidget,
                             QTableWidgetItem, QHeaderView, QComboBox, QGroupBox)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QColor, QPalette, QIcon, QPixmap


def parse_steam_urls(file_content):
    lines = file_content.strip().split('\n')
    if not lines:
        return None, []

    app_id = None
    mod_ids = []


    first_line = lines[0].strip()
    if first_line.startswith("https://store.steampowered.com/app/"):
        app_match = re.search(r'/app/(\d+)/', first_line)
        if app_match:
            app_id = app_match.group(1)
    else:

        if first_line.isdigit():
            mod_ids.append(first_line)

    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue

        if "steamcommunity.com/sharedfiles/filedetails/" in line:
            mod_match = re.search(r'\?id=(\d+)', line)
            if mod_match:
                mod_ids.append(mod_match.group(1))
        elif "steamcommunity.com/workshop/filedetails/" in line:
            mod_match = re.search(r'\?id=(\d+)', line)
            if mod_match:
                mod_ids.append(mod_match.group(1))
        elif line.isdigit():
            mod_ids.append(line)

    return app_id, mod_ids

class DownloaderWorker(QThread):
    progress_update = pyqtSignal(dict)
    finished = pyqtSignal(int)

    def __init__(self, app_id, workshop_ids, steamcmd_path, steam_folder, target_folder, mod_names, max_parallel_downloads):
        super().__init__()
        self.app_id = app_id
        self.workshop_ids = workshop_ids
        self.steamcmd_path = steamcmd_path
        self.steam_folder = steam_folder
        self.target_folder = target_folder
        self.mod_names = mod_names
        self.max_parallel_downloads = max_parallel_downloads
        self.progress_dict = {
            wid: {"status": "WAITING", "progress": 0, "name": self.mod_names.get(wid, f"Mod-{wid}")}
            for wid in workshop_ids
        }
        self.lock = threading.Lock()
        self.success_count = 0
        self.is_running = True

    def run(self):
        self.success_count = self.download_mods_parallel(self.max_parallel_downloads)
        self.finished.emit(self.success_count)

    def download_mods_parallel(self, max_workers):
        total_mods = len(self.workshop_ids)
        success_count = 0

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_id = {
                executor.submit(
                    self.download_and_move_mod,
                    self.app_id,
                    workshop_id,
                    self.steamcmd_path,
                    self.steam_folder,
                    self.target_folder,
                    i + 1,
                    total_mods
                ): workshop_id for i, workshop_id in enumerate(self.workshop_ids)
            }

            for future in concurrent.futures.as_completed(future_to_id):
                if not self.is_running:
                    executor.shutdown(wait=False)
                    break

                workshop_id = future_to_id[future]
                try:
                    if future.result():
                        success_count += 1
                except Exception as e:
                    with self.lock:
                        self.progress_dict[workshop_id] = {"status": "ERROR", "progress": 0, "error": str(e)}
                        self.progress_update.emit(self.progress_dict.copy())

        return success_count

    def download_and_move_mod(self, app_id, workshop_id, steamcmd_path, steam_folder, target_folder, index, total):
        max_retries = 3
        retry_count = 0
        success = False

        while retry_count < max_retries and not success:
            try:
                if not self.is_running:
                    return False

                mod_name = self.mod_names.get(workshop_id, f"Mod-{workshop_id}")

                with self.lock:
                    self.progress_dict[workshop_id] = {"status": "DOWNLOADING", "progress": 0, "name": mod_name}
                    self.progress_update.emit(self.progress_dict.copy())

                cmd = f'"{steamcmd_path}" +force_install_dir "{steam_folder}" +login anonymous +workshop_download_item {app_id} {workshop_id} validate +quit'
                process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

                total_size = None
                downloaded_size = 0

                for line in process.stdout:
                    if not self.is_running:
                        process.terminate()
                        return False

                    size_match = re.search(r"Size: (\d+)", line)
                    if size_match:
                        total_size = int(size_match.group(1))

                    progress_match = re.search(r"Progress: (\d+)%", line) or re.search(r"(\d+) bytes", line)
                    if progress_match and total_size:
                        if "bytes" in line:
                            downloaded_size = int(progress_match.group(1))
                            progress = min(90, int((downloaded_size / total_size) * 100))
                        else:
                            progress = min(90, int(progress_match.group(1)))
                        with self.lock:
                            self.progress_dict[workshop_id]["progress"] = progress
                            self.progress_update.emit(self.progress_dict.copy())

                    if "Success" in line:
                        with self.lock:
                            self.progress_dict[workshop_id]["progress"] = 90
                            self.progress_update.emit(self.progress_dict.copy())

                process.wait()

                if process.returncode != 0:
                    retry_count += 1
                    with self.lock:
                        self.progress_dict[workshop_id] = {
                            "status": "RETRYING",
                            "progress": 0,
                            "error": f"Retry {retry_count}/{max_retries}",
                            "name": mod_name
                        }
                        self.progress_update.emit(self.progress_dict.copy())
                    time.sleep(2)
                    continue


                mod_source = f"\\\\?\\{os.path.join(steam_folder, 'steamapps', 'workshop', 'content', app_id, workshop_id)}"
                if not os.path.exists(mod_source):
                    retry_count += 1
                    continue

                with self.lock:
                    self.progress_dict[workshop_id] = {"status": "MOVING", "progress": 95, "name": mod_name}
                    self.progress_update.emit(self.progress_dict.copy())


                os.makedirs(f"\\\\?\\{target_folder}", exist_ok=True)
                mod_target = f"\\\\?\\{os.path.join(target_folder, workshop_id)}"

                if os.path.exists(mod_target):
                    shutil.rmtree(mod_target)

                shutil.copytree(mod_source, mod_target)

                with self.lock:
                    self.progress_dict[workshop_id] = {"status": "COMPLETED", "progress": 100, "name": mod_name}
                    self.progress_update.emit(self.progress_dict.copy())
                success = True
                return True

            except Exception as e:
                retry_count += 1
                with self.lock:
                    self.progress_dict[workshop_id] = {
                        "status": "RETRYING" if retry_count < max_retries else "ERROR",
                        "progress": 0,
                        "error": f"Retry {retry_count}/{max_retries}" if retry_count < max_retries else str(e),
                        "name": mod_name
                    }
                    self.progress_update.emit(self.progress_dict.copy())
                time.sleep(2)

        if not success:
            with self.lock:
                self.progress_dict[workshop_id]["status"] = "ERROR"
                self.progress_update.emit(self.progress_dict.copy())
            return False

    def terminate(self):
        self.is_running = False
        super().terminate()


class CollectionWorker(QThread):
    result = pyqtSignal(list, str, str)
    error = pyqtSignal(str)
    progress = pyqtSignal(int, str)

    def __init__(self, collection_id):
        super().__init__()
        self.collection_id = collection_id

    def run(self):
        try:
            self.progress.emit(10, "Connecting...")
            mod_data, app_id = self.extract_workshop_collection_mods(self.collection_id)

            if mod_data:
                self.progress.emit(100, f"{len(mod_data)} mods found.")
                self.result.emit(mod_data, self.collection_id, app_id)
            else:
                self.error.emit("No mods found in collection.")
        except Exception as e:
            self.error.emit(f"An error occurred: {e}")

    def extract_workshop_collection_mods(self, collection_id):
        try:
            url = f"https://steamcommunity.com/sharedfiles/filedetails/?id={collection_id}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Referer": "https://steamcommunity.com/"
            }

            self.progress.emit(30, "Getting data from Steam...")
            response = requests.get(url, headers=headers)

            if response.status_code != 200:
                self.error.emit(f"Error: {response.status_code} - Connection failed")
                return [], None

            app_id_match = re.search(r'appid="(\d+)"', response.text)
            app_id = app_id_match.group(1) if app_id_match else None

            if not app_id:
                app_id_match = re.search(r'https://store.steampowered.com/app/(\d+)/', response.text)
                app_id = app_id_match.group(1) if app_id_match else None

            if not app_id:
                self.error.emit("Could not determine the AppID for this collection.")
                return [], None

            self.progress.emit(50, "Parsing mod information...")
            mod_ids = set()
            mod_data = []

            ids = re.findall(
                r'href="https://steamcommunity\.com/sharedfiles/filedetails/\?id=(\d+)".*?class="collectionItem"',
                response.text, re.DOTALL
            )
            mod_ids.update(ids)

            if not mod_ids:
                self.progress.emit(70, "Trying alternative search method...")
                ids = re.findall(r'data-publishedfileid="(\d+)"', response.text)
                mod_ids.update(ids)

            if not mod_ids:
                self.progress.emit(85, "Trying final search method...")
                ids = re.findall(r'SharedFileBindMouseHover\(\s*[\'\"](\d+)[\'\"]', response.text)
                mod_ids.update(ids)

            for mod_id in mod_ids:

                name_pattern = rf'href="https://steamcommunity\.com/sharedfiles/filedetails/\?id={mod_id}".*?class="collectionItem.*?<a.*?>(.*?)</a>'
                name_match = re.search(name_pattern, response.text, re.DOTALL)
                if name_match:
                    mod_name = re.sub(r'<[^>]+>', '', name_match.group(1)).strip()
                else:
                    alt_name_match = re.search(
                        rf'data-publishedfileid="{mod_id}".*?<div class="workshopItemTitle">(.*?)</div>',
                        response.text, re.DOTALL
                    )
                    mod_name = re.sub(r'<[^>]+>', '',
                                      alt_name_match.group(1)).strip() if alt_name_match else f"Mod-{mod_id}"

                mod_data.append({"id": mod_id, "name": mod_name})

            if not mod_data:
                self.error.emit("No mods could be parsed from the collection.")
                return [], app_id

            self.progress.emit(100, f"Found {len(mod_data)} mods.")
            return mod_data, app_id

        except Exception as e:
            self.error.emit(f"An error occurred: {e}")
            return [], None

class SteamWorkshopDownloaderGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.mod_names = {}

        self.setWindowTitle("Steam Workshop Mod Downloader")
        self.setMinimumSize(800, 600)

        self.apply_dark_theme()

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)

        self.current_dir = os.getcwd()
        self.steam_folder = os.path.join(self.current_dir, 'steam')
        self.steamcmd_path = os.path.join(self.steam_folder, 'steamcmd.exe')
        self.mods_file = os.path.join(self.current_dir, 'mods.txt')
        self.target_folder = os.path.join(self.current_dir, 'mods')
        self.max_parallel_downloads = 5
        self.create_tab_widget()

        self.status_bar = self.statusBar()
        self.status_bar.showMessage("Ready")

        self.downloader_thread = None
        self.collection_thread = None
        self.ui_update_timer = QTimer()
        self.ui_update_timer.timeout.connect(self.update_ui_from_cached_data)
        self.cached_progress_data = {}

        self.mod_ids = []
        self.app_id = ""

        self.check_steamcmd()

    def open_mods_folder(self):
        target_folder = f"\\\\?\\{self.target_folder}"
        if not os.path.exists(target_folder):
            CustomMessageBox.warning(self, "Folder Not Found", "The mods folder does not exist.")
            return

        try:
            if sys.platform == "win32":
                os.startfile(target_folder)
            elif sys.platform == "darwin":
                subprocess.run(["open", target_folder])
            else:
                subprocess.run(["xdg-open", target_folder])
        except Exception as e:
            CustomMessageBox.warning(self, "Error", f"Failed to open mods folder: {str(e)}")

    def delete_selected_mods(self):
        selected_rows = self.mods_folder_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "No Selection", "Please select at least one mod to delete.")
            return

        reply = QMessageBox.question(
            self, "Confirm Deletion",
            f"Are you sure you want to delete {len(selected_rows)} mod(s)?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            for row in selected_rows:
                mod_id = self.mods_folder_table.item(row.row(), 0).text()
                mod_path = f"\\\\?\\{os.path.join(self.target_folder, mod_id)}"
                if os.path.exists(mod_path):
                    try:
                        shutil.rmtree(mod_path)
                    except Exception as e:
                        QMessageBox.warning(self, "Error", f"Failed to delete mod {mod_id}: {str(e)}")
            self.update_mods_folder_table()
            QMessageBox.information(self, "Success", f"{len(selected_rows)} mod(s) deleted successfully.")

    def delete_all_mods(self):
        if not os.path.exists(self.target_folder) or not os.listdir(self.target_folder):
            QMessageBox.warning(self, "No Mods", "The mods folder is already empty.")
            return

        reply = QMessageBox.question(
            self, "Confirm Deletion",
            "Are you sure you want to delete all mods?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            try:
                for mod_id in os.listdir(self.target_folder):
                    mod_path = f"\\\\?\\{os.path.join(self.target_folder, mod_id)}"
                    if os.path.exists(mod_path):
                        shutil.rmtree(mod_path)
                self.update_mods_folder_table()
                QMessageBox.information(self, "Success", "All mods deleted successfully.")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to delete mods: {str(e)}")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_A and event.modifiers() == Qt.ControlModifier:
            if self.tabs.currentIndex() == 4:
                self.mods_folder_table.selectAll()
        super().keyPressEvent(event)

    def update_mods_folder_table(self):
        self.mods_folder_table.setRowCount(0)
        mod_count = 0
        if os.path.exists(self.target_folder):
            mod_ids = os.listdir(self.target_folder)
            mod_count = len(mod_ids)
            for mod_id in mod_ids:
                row = self.mods_folder_table.rowCount()
                self.mods_folder_table.insertRow(row)
                id_item = QTableWidgetItem(mod_id)
                id_item.setFlags(id_item.flags() & ~Qt.ItemIsEditable)
                self.mods_folder_table.setItem(row, 0, id_item)

        self.mod_count_label.setText(f"Current Mod Count: {mod_count}")

    def apply_dark_theme(self):
        app = QApplication.instance()

        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(53, 53, 53))
        palette.setColor(QPalette.WindowText, Qt.white)
        palette.setColor(QPalette.Base, QColor(25, 25, 25))
        palette.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
        palette.setColor(QPalette.ToolTipBase, Qt.white)
        palette.setColor(QPalette.ToolTipText, Qt.white)
        palette.setColor(QPalette.Text, Qt.white)
        palette.setColor(QPalette.Button, QColor(53, 53, 53))
        palette.setColor(QPalette.ButtonText, Qt.white)
        palette.setColor(QPalette.BrightText, Qt.red)
        palette.setColor(QPalette.Link, QColor(42, 130, 218))
        palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
        palette.setColor(QPalette.HighlightedText, Qt.black)

        app.setPalette(palette)

        app.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #353535;
                color: white;
            }

            QPushButton {
                background-color: #1e88e5;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }

            QPushButton:hover {
                background-color: #42a5f5;
            }

            QPushButton:pressed {
                background-color: #0d47a1;
            }

            QPushButton:disabled {
                background-color: #555555;
                color: #888888;
            }

            QLineEdit, QTextEdit, QTableWidget, QComboBox {
                background-color: #424242;
                color: white;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 4px;
            }

            QProgressBar {
                border: 1px solid #555555;
                border-radius: 4px;
                text-align: center;
                background-color: #424242;
            }

            QProgressBar::chunk {
                background-color: #1e88e5;
                border-radius: 3px;
            }

            QTabWidget::pane {
                border: 1px solid #555555;
                background-color: #353535;
                border-radius: 4px;
            }

            QTabBar::tab {
                background-color: #424242;
                color: white;
                padding: 8px 16px;
                margin-right: 2px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }

            QTabBar::tab:selected {
                background-color: #1e88e5;
            }

            QTabBar::tab:hover:!selected {
                background-color: #555555;
            }

            QHeaderView::section {
                background-color: #424242;
                color: white;
                padding: 4px;
                border: 1px solid #555555;
            }

            QGroupBox {
                border: 1px solid #555555;
                border-radius: 4px;
                margin-top: 12px;
                padding-top: 12px;
            }

            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px;
            }
        """)

    def create_tab_widget(self):
        self.tabs = QTabWidget()

        self.main_tab = QWidget()
        self.main_tab_layout = QVBoxLayout(self.main_tab)

        welcome_label = QLabel("STEAM WORKSHOP MOD DOWNLOADER")
        welcome_label.setAlignment(Qt.AlignCenter)
        welcome_label.setFont(QFont("Arial", 18, QFont.Bold))
        welcome_label.setStyleSheet("color: #1e88e5; margin: 20px 0;")

        btn_layout = QVBoxLayout()

        self.btn_from_file = QPushButton("Download from mods.txt File")
        self.btn_from_file.setFixedHeight(50)
        self.btn_from_file.clicked.connect(self.download_from_file)

        self.btn_from_collection = QPushButton("Download from Workshop Collection")
        self.btn_from_collection.setFixedHeight(50)
        self.btn_from_collection.clicked.connect(lambda: self.tabs.setCurrentIndex(1))

        self.btn_settings = QPushButton("Settings")
        self.btn_settings.setFixedHeight(50)
        self.btn_settings.clicked.connect(lambda: self.tabs.setCurrentIndex(2))

        btn_layout.addWidget(self.btn_from_file)
        btn_layout.addWidget(self.btn_from_collection)
        btn_layout.addWidget(self.btn_settings)
        btn_layout.setSpacing(10)

        self.status_group = QGroupBox("Status")
        status_layout = QVBoxLayout(self.status_group)

        self.steamcmd_status = QLabel("SteamCMD Status: Checking...")
        self.steamcmd_status.setStyleSheet("font-weight: bold;")

        status_layout.addWidget(self.steamcmd_status)

        self.main_tab_layout.addWidget(welcome_label)
        self.main_tab_layout.addLayout(btn_layout)
        self.main_tab_layout.addWidget(self.status_group)
        self.main_tab_layout.addStretch()

        self.collection_tab = QWidget()
        self.collection_tab_layout = QVBoxLayout(self.collection_tab)

        collection_title = QLabel("Download from Workshop Collection")
        collection_title.setAlignment(Qt.AlignCenter)
        collection_title.setFont(QFont("Arial", 16, QFont.Bold))
        collection_title.setStyleSheet("color: #1e88e5; margin: 10px 0;")

        form_layout = QVBoxLayout()

        collection_id_layout = QHBoxLayout()
        collection_id_label = QLabel("Collection ID:")
        self.collection_id_input = QLineEdit()
        self.collection_id_input.setPlaceholderText("Example: 1234567890")
        collection_id_layout.addWidget(collection_id_label)
        collection_id_layout.addWidget(self.collection_id_input)

        collection_btn_layout = QHBoxLayout()
        self.btn_fetch_collection = QPushButton("Fetch Collection")
        self.btn_fetch_collection.clicked.connect(self.fetch_collection)

        self.btn_download_collection = QPushButton("Download Mods")
        self.btn_download_collection.setEnabled(False)
        self.btn_download_collection.clicked.connect(self.download_collection)

        collection_btn_layout.addWidget(self.btn_fetch_collection)
        collection_btn_layout.addWidget(self.btn_download_collection)

        self.collection_progress = QProgressBar()
        self.collection_progress.setValue(0)
        self.collection_progress.setTextVisible(True)
        self.collection_progress.setFormat("%p% - %v")

        mod_list_label = QLabel("Found Mods:")
        self.mod_list_table = QTableWidget(0, 2)
        self.mod_list_table.setHorizontalHeaderLabels(["Mod ID", "Name"])
        self.mod_list_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.mod_list_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.mod_list_table.setSelectionBehavior(QTableWidget.SelectRows)

        form_layout.addLayout(collection_id_layout)
        form_layout.addLayout(collection_btn_layout)
        form_layout.addWidget(self.collection_progress)

        self.collection_tab_layout.addWidget(collection_title)
        self.collection_tab_layout.addLayout(form_layout)
        self.collection_tab_layout.addWidget(mod_list_label)
        self.collection_tab_layout.addWidget(self.mod_list_table)

        self.settings_tab = QWidget()
        self.settings_tab_layout = QVBoxLayout(self.settings_tab)

        settings_title = QLabel("Settings")
        settings_title.setAlignment(Qt.AlignCenter)
        settings_title.setFont(QFont("Arial", 16, QFont.Bold))
        settings_title.setStyleSheet("color: #1e88e5; margin: 10px 0;")

        paths_group = QGroupBox("File Locations")
        paths_layout = QVBoxLayout(paths_group)

        steamcmd_path_layout = QHBoxLayout()
        steamcmd_path_label = QLabel("SteamCMD Folder:")
        self.steamcmd_path_input = QLineEdit()
        self.steamcmd_path_input.setText(self.steam_folder)
        self.steamcmd_path_input.setReadOnly(True)
        steamcmd_browse_btn = QPushButton("Browse")
        steamcmd_browse_btn.clicked.connect(lambda: self.browse_folder("steamcmd"))

        steamcmd_path_layout.addWidget(steamcmd_path_label)
        steamcmd_path_layout.addWidget(self.steamcmd_path_input)
        steamcmd_path_layout.addWidget(steamcmd_browse_btn)

        target_path_layout = QHBoxLayout()
        target_path_label = QLabel("Mods Folder:")
        self.target_path_input = QLineEdit()
        self.target_path_input.setText(self.target_folder)
        self.target_path_input.setReadOnly(True)
        target_browse_btn = QPushButton("Browse")
        target_browse_btn.clicked.connect(lambda: self.browse_folder("target"))

        target_path_layout.addWidget(target_path_label)
        target_path_layout.addWidget(self.target_path_input)
        target_path_layout.addWidget(target_browse_btn)

        paths_layout.addLayout(steamcmd_path_layout)
        paths_layout.addLayout(target_path_layout)

        downloads_group = QGroupBox("Download Settings")
        downloads_layout = QVBoxLayout(downloads_group)

        max_downloads_layout = QHBoxLayout()
        max_downloads_label = QLabel("Max Parallel Downloads:")
        self.max_downloads_input = QComboBox()
        self.max_downloads_input.addItems([str(i) for i in range(1, 11)])
        self.max_downloads_input.setCurrentText("4")
        max_downloads_layout.addWidget(max_downloads_label)
        max_downloads_layout.addWidget(self.max_downloads_input)

        downloads_layout.addLayout(max_downloads_layout)

        self.btn_save_settings = QPushButton("Save Settings")
        self.btn_save_settings.clicked.connect(self.save_settings)

        self.settings_tab_layout.addWidget(settings_title)
        self.settings_tab_layout.addWidget(paths_group)
        self.settings_tab_layout.addWidget(downloads_group)
        self.settings_tab_layout.addWidget(self.btn_save_settings)
        self.settings_tab_layout.addStretch()

        self.download_tab = QWidget()
        self.download_tab_layout = QVBoxLayout(self.download_tab)

        download_title = QLabel("Mod Download Progress")
        download_title.setAlignment(Qt.AlignCenter)
        download_title.setFont(QFont("Arial", 16, QFont.Bold))
        download_title.setStyleSheet("color: #1e88e5; margin: 10px 0;")

        self.overall_progress_label = QLabel("Overall Progress: 0/0")
        self.overall_progress = QProgressBar()
        self.overall_progress.setFormat("%p% (%v/%m)")

        active_downloads_group = QGroupBox("Active Downloads")
        active_downloads_layout = QVBoxLayout(active_downloads_group)

        self.active_downloads_table = QTableWidget(0, 4)
        self.active_downloads_table.setHorizontalHeaderLabels(["Mod ID", "Name", "Status", "Progress"])
        self.active_downloads_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.active_downloads_table.setColumnWidth(0, 150)
        self.active_downloads_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.active_downloads_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.active_downloads_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)

        active_downloads_layout.addWidget(self.active_downloads_table)

        completed_group = QGroupBox("Completed Mods")
        completed_layout = QVBoxLayout(completed_group)

        self.completed_table = QTableWidget(0, 2)
        self.completed_table.setHorizontalHeaderLabels(["Mod ID", "Name"])
        self.completed_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.completed_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)


        completed_layout.addWidget(self.completed_table)

        error_group = QGroupBox("Failed Mods")
        error_layout = QVBoxLayout(error_group)

        self.error_table = QTableWidget(0, 3)
        self.error_table.setHorizontalHeaderLabels(["Mod ID", "Name", "Error"])
        self.error_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.error_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.error_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)

        error_layout.addWidget(self.error_table)

        self.btn_cancel_download = QPushButton("Cancel Download")
        self.btn_cancel_download.clicked.connect(self.cancel_download)

        self.download_tab_layout.addWidget(download_title)
        self.download_tab_layout.addWidget(self.overall_progress_label)
        self.download_tab_layout.addWidget(self.overall_progress)
        self.download_tab_layout.addWidget(active_downloads_group)

        bottom_tables_layout = QHBoxLayout()
        bottom_tables_layout.addWidget(completed_group)
        bottom_tables_layout.addWidget(error_group)
        bottom_tables_layout.setSpacing(20)

        self.download_tab_layout.addLayout(bottom_tables_layout)
        self.download_tab_layout.addWidget(self.btn_cancel_download)

        self.tabs.addTab(self.main_tab, "Main Menu")
        self.tabs.addTab(self.collection_tab, "Collection")
        self.tabs.addTab(self.settings_tab, "Settings")
        self.tabs.addTab(self.download_tab, "Download Progress")

        self.main_layout.addWidget(self.tabs)

        self.mods_folder_tab = QWidget()
        self.mods_folder_tab_layout = QVBoxLayout(self.mods_folder_tab)

        mods_folder_title = QLabel("Mods Folder")
        mods_folder_title.setAlignment(Qt.AlignCenter)
        mods_folder_title.setFont(QFont("Arial", 16, QFont.Bold))
        mods_folder_title.setStyleSheet("color: #1e88e5; margin: 10px 0;")


        self.mod_count_label = QLabel("Current Mod Count: 0")
        self.mod_count_label.setAlignment(Qt.AlignCenter)
        self.mod_count_label.setStyleSheet("color: #E0E0E0; margin: 5px 0;")


        self.mods_folder_table = QTableWidget(0, 1)
        self.mods_folder_table.setHorizontalHeaderLabels(["Mod ID"])
        self.mods_folder_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.mods_folder_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.mods_folder_table.setSelectionMode(QTableWidget.ExtendedSelection)


        mods_folder_btn_layout = QHBoxLayout()
        self.btn_open_folder = QPushButton("Open Folder")
        self.btn_open_folder.clicked.connect(self.open_mods_folder)

        self.btn_delete_selected = QPushButton("Delete Selected")
        self.btn_delete_selected.clicked.connect(self.delete_selected_mods)

        self.btn_delete_all = QPushButton("Delete All")
        self.btn_delete_all.clicked.connect(self.delete_all_mods)

        mods_folder_btn_layout.addWidget(self.btn_open_folder)
        mods_folder_btn_layout.addWidget(self.btn_delete_selected)
        mods_folder_btn_layout.addWidget(self.btn_delete_all)

        self.mods_folder_tab_layout.addWidget(mods_folder_title)
        self.mods_folder_tab_layout.addWidget(self.mod_count_label)
        self.mods_folder_tab_layout.addWidget(self.mods_folder_table)
        self.mods_folder_tab_layout.addLayout(mods_folder_btn_layout)

        self.tabs.addTab(self.mods_folder_tab, "Mods Folder")


        self.update_mods_folder_table()

    def check_steamcmd(self):
        if os.path.exists(self.steamcmd_path):
            self.steamcmd_status.setText("SteamCMD Status: ✅ Ready")
            self.steamcmd_status.setStyleSheet("font-weight: bold; color: #4CAF50;")
            return True
        else:
            self.steamcmd_status.setText("SteamCMD Status: ❌ Not Found")
            self.steamcmd_status.setStyleSheet("font-weight: bold; color: #F44336;")
            QMessageBox.warning(self, "SteamCMD Not Found",
                                "SteamCMD not found. Please download SteamCMD and extract it to the 'steam' folder.")
            return False

    def browse_folder(self, folder_type):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder", os.getcwd())

        if folder:
            if folder_type == "steamcmd":
                self.steam_folder = folder
                self.steamcmd_path = os.path.join(folder, 'steamcmd.exe')
                self.steamcmd_path_input.setText(folder)
                self.check_steamcmd()
            elif folder_type == "target":
                self.target_folder = folder
                self.target_path_input.setText(folder)

    def save_settings(self):
        self.steam_folder = self.steamcmd_path_input.text()
        self.steamcmd_path = os.path.join(self.steam_folder, 'steamcmd.exe')
        self.target_folder = self.target_path_input.text()

        self.max_parallel_downloads = int(self.max_downloads_input.currentText())

        if self.check_steamcmd():
            QMessageBox.information(self, "Settings Saved", "Settings saved successfully.")
        else:
            QMessageBox.warning(self, "SteamCMD Error",
                                "SteamCMD not found at the specified location. Please select a valid location.")

    def get_app_id_from_mod_id(self, mod_id):
        try:
            url = f"https://steamcommunity.com/sharedfiles/filedetails/?id={mod_id}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Referer": "https://steamcommunity.com/"
            }
            response = requests.get(url, headers=headers)
            if response.status_code != 200:
                return None

            app_id_match = re.search(r'appid="(\d+)"', response.text)
            if app_id_match:
                return app_id_match.group(1)

            app_id_match = re.search(r'https://store.steampowered.com/app/(\d+)/', response.text)
            return app_id_match.group(1) if app_id_match else None
        except Exception:
            return None

    def download_from_file(self):
        if not self.check_steamcmd():
            return

        if not os.path.exists(self.mods_file):
            QMessageBox.warning(self, "File Not Found",
                                "mods.txt file not found. Please create the file.")
            return

        try:
            with open(self.mods_file, 'r') as f:
                file_content = f.read()

            self.app_id, self.mod_ids = parse_steam_urls(file_content)

            if not self.mod_ids:
                QMessageBox.warning(self, "No Mods Found", "No mod IDs found in mods.txt file.")
                return


            if not self.app_id and self.mod_ids:
                self.app_id = self.get_app_id_from_mod_id(self.mod_ids[0])
                if not self.app_id:
                    QMessageBox.warning(self, "Error", "Could not determine the AppID for the mods.")
                    return


            self.mod_names = {mod_id: f"Mod-{mod_id}" for mod_id in self.mod_ids}
            self.start_download()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"File reading error: {str(e)}")

    def fetch_collection(self):
        collection_id = self.collection_id_input.text().strip()

        if not collection_id:
            QMessageBox.warning(self, "Error", "Please enter a valid collection ID.")
            return


        if "steamcommunity.com/sharedfiles/filedetails/" in collection_id:
            coll_match = re.search(r'\?id=(\d+)', collection_id)
            if coll_match:
                collection_id = coll_match.group(1)

        self.btn_fetch_collection.setEnabled(False)
        self.collection_progress.setValue(0)
        self.mod_list_table.setRowCount(0)
        self.status_bar.showMessage("Fetching collection...")

        self.collection_thread = CollectionWorker(collection_id)
        self.collection_thread.result.connect(self.on_collection_fetched)
        self.collection_thread.error.connect(self.on_collection_error)
        self.collection_thread.progress.connect(self.on_collection_progress)
        self.collection_thread.start()

    def on_collection_progress(self, value, message):
        self.collection_progress.setValue(value)
        self.status_bar.showMessage(message)

    def on_collection_fetched(self, mod_data, collection_id, app_id):
        self.mod_ids = [mod["id"] for mod in mod_data]
        self.mod_names = {mod["id"]: mod["name"] for mod in mod_data}
        self.app_id = app_id
        self.btn_fetch_collection.setEnabled(True)

        self.mod_list_table.setRowCount(len(mod_data))

        for i, mod in enumerate(mod_data):
            id_item = QTableWidgetItem(mod["id"])
            id_item.setFlags(id_item.flags() & ~Qt.ItemIsEditable)

            name_item = QTableWidgetItem(mod["name"])
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)

            self.mod_list_table.setItem(i, 0, id_item)
            self.mod_list_table.setItem(i, 1, name_item)

        self.btn_download_collection.setEnabled(True)
        self.status_bar.showMessage(f"Collection fetched: {len(mod_data)} mods found")

    def on_collection_error(self, error_msg):
        self.btn_fetch_collection.setEnabled(True)
        self.status_bar.showMessage("Error fetching collection")
        QMessageBox.critical(self, "Collection Error", error_msg)

    def download_collection(self):
        if not self.app_id:
            QMessageBox.warning(self, "Error", "Could not determine the AppID for this collection.")
            return

        self.start_download()

    def start_download(self):
        if not self.mod_ids:
            QMessageBox.warning(self, "No Mods", "No mods to download.")
            return

        if not self.check_steamcmd():
            return

        self.active_downloads_table.setRowCount(0)
        self.completed_table.setRowCount(0)
        self.error_table.setRowCount(0)

        self.overall_progress.setMaximum(len(self.mod_ids))
        self.overall_progress.setValue(0)
        self.overall_progress_label.setText(f"Overall Progress: 0/{len(self.mod_ids)}")

        self.tabs.setCurrentIndex(3)

        self.downloader_thread = DownloaderWorker(
            self.app_id,
            self.mod_ids,
            self.steamcmd_path,
            self.steam_folder,
            self.target_folder,
            self.mod_names,
            self.max_parallel_downloads
        )

        self.downloader_thread.progress_update.connect(self.handle_progress_update)
        self.downloader_thread.finished.connect(self.on_download_finished)

        self.ui_update_timer.start(500)

        self.downloader_thread.start()
        self.status_bar.showMessage("Downloading mods...")
        self.btn_cancel_download.setEnabled(True)

    def handle_progress_update(self, progress_dict):
        self.cached_progress_data = progress_dict

    def update_ui_from_cached_data(self):
        if not self.cached_progress_data:
            return

        progress_dict = self.cached_progress_data

        completed_count = 0
        error_count = 0

        self.active_downloads_table.setRowCount(0)
        self.completed_table.setRowCount(0)
        self.error_table.setRowCount(0)

        active_count = 0
        completed_rows = []
        error_rows = []

        for mod_id, data in progress_dict.items():
            status = data.get("status", "UNKNOWN")
            progress = data.get("progress", 0)
            mod_name = data.get("name", f"Mod-{mod_id}")
            error = data.get("error", "")

            if status == "COMPLETED":
                completed_count += 1
                completed_rows.append((mod_id, mod_name))
            elif status == "ERROR":
                error_count += 1
                error_rows.append((mod_id, mod_name, error))
            elif status in ["DOWNLOADING", "MOVING", "WAITING", "RETRYING"]:
                active_count += 1

                row = self.active_downloads_table.rowCount()
                self.active_downloads_table.insertRow(row)

                id_item = QTableWidgetItem(mod_id)
                id_item.setFlags(id_item.flags() & ~Qt.ItemIsEditable)

                name_item = QTableWidgetItem(mod_name)
                name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)

                status_item = QTableWidgetItem(status)
                status_item.setFlags(status_item.flags() & ~Qt.ItemIsEditable)

                progress_item = QTableWidgetItem(f"{progress}%")
                progress_item.setFlags(progress_item.flags() & ~Qt.ItemIsEditable)

                self.active_downloads_table.setItem(row, 0, id_item)
                self.active_downloads_table.setItem(row, 1, name_item)
                self.active_downloads_table.setItem(row, 2, status_item)
                self.active_downloads_table.setItem(row, 3, progress_item)


        for i, (mod_id, mod_name) in enumerate(completed_rows):
            self.completed_table.insertRow(i)

            id_item = QTableWidgetItem(mod_id)
            id_item.setFlags(id_item.flags() & ~Qt.ItemIsEditable)

            name_item = QTableWidgetItem(mod_name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)

            self.completed_table.setItem(i, 0, id_item)
            self.completed_table.setItem(i, 1, name_item)


        for i, (mod_id, mod_name, error) in enumerate(error_rows):
            self.error_table.insertRow(i)

            id_item = QTableWidgetItem(mod_id)
            id_item.setFlags(id_item.flags() & ~Qt.ItemIsEditable)

            name_item = QTableWidgetItem(mod_name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)

            error_item = QTableWidgetItem(error)
            error_item.setFlags(error_item.flags() & ~Qt.ItemIsEditable)

            self.error_table.setItem(i, 0, id_item)
            self.error_table.setItem(i, 1, name_item)
            self.error_table.setItem(i, 2, error_item)


        total_processed = completed_count + error_count
        self.overall_progress.setValue(total_processed)
        self.overall_progress_label.setText(f"Overall Progress: {total_processed}/{len(self.mod_ids)}")

        self.status_bar.showMessage(f"Active: {active_count} | Completed: {completed_count} | Failed: {error_count}")

    def on_download_finished(self, success_count):
        self.ui_update_timer.stop()
        self.update_ui_from_cached_data()

        total_mods = len(self.mod_ids)
        failed_count = total_mods - success_count

        self.btn_cancel_download.setEnabled(False)
        self.status_bar.showMessage(f"Download completed. Success: {success_count}, Failed: {failed_count}")

        QMessageBox.information(
            self,
            "Download Complete",
            f"Download completed.\n\nTotal mods: {total_mods}\nSuccess: {success_count}\nFailed: {failed_count}"
        )


        self.update_mods_folder_table()

    def cancel_download(self):
        reply = QMessageBox.question(
            self,
            "Cancel Download",
            "Are you sure you want to cancel the download?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            if self.downloader_thread and self.downloader_thread.isRunning():
                self.downloader_thread.is_running = False
                self.ui_update_timer.stop()
                self.status_bar.showMessage("Download canceled.")
                self.btn_cancel_download.setEnabled(False)

                self.tabs.setCurrentIndex(0)

                self.btn_from_file.setEnabled(True)
                self.btn_from_collection.setEnabled(True)

                if hasattr(self, 'btn_download_collection'):
                    self.btn_download_collection.setEnabled(False)
                if hasattr(self, 'btn_fetch_collection'):
                    self.btn_fetch_collection.setEnabled(True)
def main():
    app = QApplication(sys.argv)
    colorama.init()
    window = SteamWorkshopDownloaderGUI()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

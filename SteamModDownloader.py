import os
import subprocess
import shutil
import time
import sys
import concurrent.futures
import threading
import colorama
from colorama import Fore, Style, Back
import psutil


colorama.init(autoreset=True)


def print_header(text):
    width = 70
    print("\n" + Fore.CYAN + Style.BRIGHT + "┏" + "━" * (width - 2) + "┓")
    print(Fore.CYAN + Style.BRIGHT + "┃" + text.center(width - 2) + "┃")
    print(Fore.CYAN + Style.BRIGHT + "┗" + "━" * (width - 2) + "┛")


def print_progress(current, total, text="", status=""):
    width = 50
    done = int(width * current / total)

    status_color = Fore.GREEN
    if status == "ERROR":
        status_color = Fore.RED
    elif status == "WARN":
        status_color = Fore.YELLOW

    progress_bar = Fore.GREEN + '█' * done + Fore.WHITE + '░' * (width - done)
    print(f"\r{text} {progress_bar} {status_color}{current}/{total} {status}", end="")
    if current == total:
        print()


def print_success(text):
    print(Fore.GREEN + Style.BRIGHT + f"✓ {text}")


def print_error(text):
    print(Fore.RED + Style.BRIGHT + f"✗ {text}")


def print_warning(text):
    print(Fore.YELLOW + Style.BRIGHT + f"⚠ {text}")


def print_info(text):
    print(Fore.CYAN + f"ℹ {text}")


def extract_app_id_from_url(url):
    try:
        if "store.steampowered.com/app" in url:
            parts = url.split('/')
            for i, part in enumerate(parts):
                if part == "app":
                    return parts[i + 1]
    except Exception as e:
        print_error(f"Error extracting App ID: {e}")
    return None


def extract_workshop_id_from_url(url):
    try:
        if "steamcommunity.com/sharedfiles/filedetails" in url and "id=" in url:
            return url.split("id=")[1].split("&")[0].strip()
    except Exception as e:
        print_error(f"Error extracting Workshop ID: {e}")
    return None


def read_mods_file(mods_file):
    if not os.path.exists(mods_file):
        print_error(f"File not found: {mods_file}")
        return None, []

    try:
        with open(mods_file, 'r', encoding='utf-8') as file:
            lines = [line.strip() for line in file.readlines() if line.strip()]

        if not lines:
            print_error("mods.txt file is empty.")
            return None, []

        app_url = lines[0]
        app_id = extract_app_id_from_url(app_url)

        if not app_id:
            print_error(f"Valid App ID not found. The first line must be a valid Steam Store URL: {app_url}")
            return None, []

        workshop_ids = []
        invalid_urls = []

        for line in lines[1:]:
            workshop_id = extract_workshop_id_from_url(line)
            if workshop_id:
                workshop_ids.append(workshop_id)
            else:
                invalid_urls.append(line)

        if invalid_urls:
            print_warning(f"{len(invalid_urls)} invalid URLs found. These URLs will be skipped.")

        if not workshop_ids:
            print_error("No valid Workshop ID found.")
            return app_id, []

        return app_id, workshop_ids

    except Exception as e:
        print_error(f"File reading error: {e}")
        return None, []


def check_steamcmd(steamcmd_path):
    if not os.path.exists(steamcmd_path):
        print_error(f"SteamCMD not found: {steamcmd_path}")
        print_info("Download SteamCMD and extract it to the 'steam' folder.")
        return False
    return True



def get_optimal_worker_count():
    cpu_count = os.cpu_count() or 2
    memory_gb = psutil.virtual_memory().total / (1024 ** 3)

    if memory_gb >= 8:
        return min(6, cpu_count)
    elif memory_gb >= 4:
        return min(4, cpu_count)
    else:
        return min(2, cpu_count)


def download_and_move_mod(app_id, workshop_id, steamcmd_path, steam_folder, target_folder, index, total, progress_dict,
                          lock):
    try:
        mod_name = f"Mod-{workshop_id}"

        with lock:
            progress_dict[workshop_id] = {"status": "DOWNLOADING", "progress": 0}
            update_progress_display(progress_dict, total)

        cmd = f'"{steamcmd_path}" +force_install_dir "{steam_folder}" +login anonymous +workshop_download_item {app_id} {workshop_id} validate +quit'

        process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   universal_newlines=True)

        for line in process.stdout:
            if "Update state" in line or "download" in line.lower():
                with lock:
                    progress_dict[workshop_id]["progress"] = min(progress_dict[workshop_id]["progress"] + 5, 90)
                    update_progress_display(progress_dict, total)

            if "Success" in line:
                with lock:
                    progress_dict[workshop_id]["progress"] = 90
                    update_progress_display(progress_dict, total)

        process.wait()

        if process.returncode != 0:
            stderr = process.stderr.read()
            with lock:
                progress_dict[workshop_id] = {"status": "ERROR", "progress": 0,
                                              "error": f"SteamCMD hatası: {process.returncode}"}
                update_progress_display(progress_dict, total)
            return False

        mod_source = os.path.join(steam_folder, 'steamapps', 'workshop', 'content', app_id, workshop_id)

        if not os.path.exists(mod_source):
            with lock:
                progress_dict[workshop_id] = {"status": "ERROR", "progress": 0,
                                              "error": "Mod could not be downloaded or was not found"}
                update_progress_display(progress_dict, total)
            return False

        try:
            workshop_json = os.path.join(mod_source, 'workshop.json')
            if os.path.exists(workshop_json):
                import json
                with open(workshop_json, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if 'title' in data:
                        mod_name = data['title']
        except:
            pass

        with lock:
            progress_dict[workshop_id] = {"status": "MOVING", "progress": 95, "name": mod_name}
            update_progress_display(progress_dict, total)

        os.makedirs(target_folder, exist_ok=True)

        mod_target = os.path.join(target_folder, workshop_id)

        if os.path.exists(mod_target):
            shutil.rmtree(mod_target)

        shutil.copytree(mod_source, mod_target)

        with lock:
            progress_dict[workshop_id] = {"status": "COMPLETED", "progress": 100, "name": mod_name}
            update_progress_display(progress_dict, total)

        return True

    except Exception as e:
        with lock:
            progress_dict[workshop_id] = {"status": "ERROR", "progress": 0, "error": str(e)}
            update_progress_display(progress_dict, total)
        return False


def update_progress_display(progress_dict, total):

    os.system('cls' if os.name == 'nt' else 'clear')


    completed = sum(1 for item in progress_dict.values() if item["status"] == "COMPLETED")
    errors = sum(1 for item in progress_dict.values() if item["status"] == "ERROR")

    print_header("STEAM MOD DOWNLOADER")


    overall_progress = (completed + errors) / total
    print(f"\n{Style.BRIGHT}Total Progress:")
    width = 50
    done = int(width * overall_progress)
    print(Fore.BLUE + '█' * done + Fore.WHITE + '░' * (width - done) + f" {completed + errors}/{total} mod processed")
    print(f"{Fore.GREEN}{completed} success {Fore.RED}{errors} error\n")


    active = {wid: info for wid, info in progress_dict.items()
              if info["status"] not in ["COMPLETED", "ERROR"]}

    if active:
        print(Style.BRIGHT + "Current Downloads:")
        for wid, info in active.items():
            status_color = Fore.CYAN
            status_text = info["status"]
            if status_text == "DOWNLOADING":
                status_text = "Downloading"
            elif status_text == "MOVING":
                status_text = "Moving"

            mod_name = info.get("name", f"Mod-{wid}")
            mod_name = mod_name[:30] + "..." if len(mod_name) > 30 else mod_name

            progress = info["progress"]
            bar_width = 30
            done = int(bar_width * progress / 100)

            print(f"{mod_name.ljust(35)} {status_color}[{status_text}] " +
                  Fore.GREEN + '█' * done + Fore.WHITE + '░' * (bar_width - done) +
                  f" {progress}%")
        print()

    completed_items = {wid: info for wid, info in progress_dict.items()
                       if info["status"] == "COMPLETED"}
    if completed_items:
        print(Style.BRIGHT + "Last completed:")
        for i, (wid, info) in enumerate(list(completed_items.items())[-5:], 1):
            mod_name = info.get("name", f"Mod-{wid}")
            mod_name = mod_name[:40] + "..." if len(mod_name) > 40 else mod_name
            print(f"{Fore.GREEN}✓ {mod_name}")

    error_items = {wid: info for wid, info in progress_dict.items()
                   if info["status"] == "ERROR"}
    if error_items:
        print(Style.BRIGHT + f"\n{Fore.RED}error occurring modes:")
        for wid, info in error_items.items():
            mod_name = info.get("name", f"Mod-{wid}")
            mod_name = mod_name[:30] + "..." if len(mod_name) > 30 else mod_name
            print(f"{Fore.RED}✗ {mod_name}: {info.get('error', 'unknown error')}")


def download_mods_parallel(app_id, workshop_ids, steamcmd_path, steam_folder, target_folder, max_workers):

    total_mods = len(workshop_ids)
    success_count = 0
    lock = threading.Lock()


    progress_dict = {wid: {"status": "WAITING", "progress": 0} for wid in workshop_ids}

    print_info(f"Parallel download is starting (maximum {max_workers} concurrent downloads)...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_id = {
            executor.submit(
                download_and_move_mod,
                app_id,
                workshop_id,
                steamcmd_path,
                steam_folder,
                target_folder,
                i + 1,
                total_mods,
                progress_dict,
                lock
            ): workshop_id for i, workshop_id in enumerate(workshop_ids)
        }

        for future in concurrent.futures.as_completed(future_to_id):
            workshop_id = future_to_id[future]
            try:
                if future.result():
                    success_count += 1
            except Exception as e:
                with lock:
                    progress_dict[workshop_id] = {"status": "ERROR", "progress": 0, "error": str(e)}
                    update_progress_display(progress_dict, total_mods)

    return success_count


def main():
    colorama.init()

    current_dir = os.getcwd()
    steam_folder = os.path.join(current_dir, 'steam')
    steamcmd_path = os.path.join(steam_folder, 'steamcmd.exe')
    mods_file = os.path.join(current_dir, 'mods.txt')
    target_folder = os.path.join(current_dir, 'mods')

    os.system('cls' if os.name == 'nt' else 'clear')

    print_header("STEAM MOD DOWNLOADER")
    print_info("Accelerated Parallel Download Mode")

    if not check_steamcmd(steamcmd_path):
        input("\nPress any key to exit...")
        return

    app_id, workshop_ids = read_mods_file(mods_file)

    if not app_id or not workshop_ids:
        print_error("Process stopped: App ID or Workshop IDs could not be retrieved.")
        input("\nPress any key to exit...")
        return

    print_success(f"App ID: {app_id}")
    print_success(f"Total number of mods: {len(workshop_ids)}")

    max_workers = get_optimal_worker_count()
    print_info(f"System analysis determined that {max_workers} parallel downloads will be used.")

    start_time = time.time()

    success_count = download_mods_parallel(app_id, workshop_ids, steamcmd_path,
                                           steam_folder, target_folder, max_workers)

    elapsed_time = time.time() - start_time
    hours, remainder = divmod(int(elapsed_time), 3600)
    minutes, seconds = divmod(remainder, 60)

    os.system('cls' if os.name == 'nt' else 'clear')

    print_header("PROCESS COMPLETED")
    print()
    print_info(f"Total number of mods: {len(workshop_ids)}")
    print_success(f"Number of successfully downloaded mods: {success_count}")

    if success_count < len(workshop_ids):
        print_error(f"Number of failed mods: {len(workshop_ids) - success_count}")

    print_info(f"Total elapsed time: {hours:02d}:{minutes:02d}:{seconds:02d}")
    print_success(f"Mods have been moved to the following folder: {target_folder}")

    input("\nPress any key to exit...")


if __name__ == '__main__':
    try:
        try:
            import colorama
            import psutil
        except ImportError:
            print("Installing required modules...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "colorama", "psutil"])
            import colorama
            import psutil

        main()
    except KeyboardInterrupt:
        print("\nProcess interrupted by user.")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
        input("\nPress any key to exit...")
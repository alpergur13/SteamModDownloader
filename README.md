# SteamModDownloader
This program is designed to download multiple mods.Either by manually downloading or collection downloading. It also tries to download more than one at a time to increase speed.

Also on version v0.2 and above we introduced collection downloading, which allows you to download entire collections. Also we improved the UI, and now program tries to download mods that failed again and again until n times, this is to prevent random failures from steamcmd.

This simply uses steamcmd to download each mod separately. 

If you need help or if you want to report bugs you can reach me via discord: halbezar 

Remember i am just a student there can be bugs. 

This program is free but if you want to support me you can via this link: https://buymeacoffee.com/alpergur

Note: Not all games can be downloaded via steamcmd anonymously, so some games need verification that you own the game, that causes an error if you try to download some mod that doesn't allow anonymous.
Here you can find out games that supports anonymous downloading: https://steamdb.info/sub/17906/apps/

# How To Download
Simply click releases and then click latest version's SteamModDownloader.rar, then you should extract all contents to a folder.

# How To Use
First of all your folder should look like this.

│── steam/

│   └── steamcmd.exe
      
│── SteamModDownloader.exe

│── mods.txt 

After this is ready you should open steamcmd, after it downloades everything it should close after a few seconds. You don't need to open steamcmd every time, once is enough.
# Manual Download
If you want to manually download mods, you must edit mods.txt file so in each row there should be mod's id(which can be found in it's steam link)

It should look like this:

![image](https://github.com/user-attachments/assets/ed63c733-16da-4167-a7e8-98080c034a01)

After that you can open SteamModDownloader, from the main menu click download from mods.txt file button, it should download all mods then copy them to mods folder.
# Collection Download
If you want to download collections you need to copy that collections id.(which can be found in collections' steam link)

After this you can open SteamModDownloader.exe and Download From Workshop Collection, then paste the collection's id, after that we can Fetch Collection then Download Mods.

Also if you want you can increase Maximum Parallel Downloads but this is about your computer and probably your ram, if it fails often then you can lower this, 4 is ideal for most pc's i think.

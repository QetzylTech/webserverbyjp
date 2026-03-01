# Minecraft Client and Server Setup
By JP

## Client TLDR
1. Install the latest:
  - TLauncher https://tlauncher.org/en/
  - Tailscale https://tailscale.com/download
2. Import modpack 
  - https://drive.google.com/drive/folders/1vuYmIkiJw5WnN9wqXW7krUkUowxOI1wr?usp=sharing
3. Join VPN using the Tailscale account provided by JP
4. Join the server using the server URL 'servera'
5. If server is off, go to http://servera/ and press start.

## Foreword
&nbsp;&nbsp;&nbsp;&nbsp;This documentation is intended for Marites only. Most instructions are aimed at the server admin.
&nbsp;&nbsp;&nbsp;&nbsp;It assumes all VPNs, tunnels, and credentials are distributed manually and are not public.
&nbsp;&nbsp;&nbsp;&nbsp;While the tutorial uses Cisco’s Modpacks, it can work with other Forge modpacks — adjust the process as needed.
&nbsp;&nbsp;&nbsp;&nbsp;For best results, follow these instructions at least once while on a call with JP or Sam.
&nbsp;&nbsp;&nbsp;&nbsp;The majority of this guide covers server setup and configuration.

## Table of Contents

&nbsp;&nbsp;&nbsp;&nbsp;[1 Client Setup](#1-client-setup)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[1.1 Install Modpack](#11-install-modpack)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[1.2 Joining VPN](#12-joining-vpn)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[1.3 Connecting to Server](#13-connecting-to-server)  
&nbsp;&nbsp;&nbsp;&nbsp;[2 Server Setup](#2-server-setup)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[2.1 Ubuntu Server Installation](#21-ubuntu-server-installation)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[2.2 Deploy Vanilla Server](#22-deploy-vanilla-server)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[2.3 Create and Distribute Modpacks](#23-create-and-distribute-modpacks)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[2.4 Deploy Modded Server (Windows)](#24-deploy-modded-server-windows)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[2.5 Deploy Server (Ubuntu CLI)](#25-deploy-server-ubuntu-cli)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[2.6 Server Properties and Other Configs](#26-server-properties-and-other-configs)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[2.7 Network Setup (VPN Path)](#27-network-setup-vpn-path)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[2.8 Network Setup (Tunnel Path)](#28-network-setup-tunnel-path)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[2.9 Server Moderation](#29-server-moderation)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[2.10 Backup and Sync (WIP uwu)](#210-backup-and-sync-wip-uwu)  
&nbsp;&nbsp;&nbsp;&nbsp;[3 General Notes and Known Issues](#3-general-notes-and-known-issues)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[3.1 Tunnels](#31-tunnels)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[3.2 Ubuntu Server Installation](#32-ubuntu-server-installation)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[3.3 Software Versions](#33-software-versions)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[3.3.1 Forge](#331-forge)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[3.4 Security](#34-security)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[3.5 Server Admin Quality of Life Tips](#35-server-admin-quality-of-life-tips)  
&nbsp;&nbsp;&nbsp;&nbsp;[4 Advanced Ubuntu Server Setup](#4-advanced-ubuntu-server-setup)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[4.1 Automatic Boot Using Systemd Service](#41-automatic-boot-using-systemd-service)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[4.2 Live Backup Guide](#42-live-backup-guide)  


## 1 Client Setup
Steps required for players to join the server.
### 1.1 Install Modpack
1. Download the modpack zip file from https://drive.google.com/drive/folders/1vuYmIkiJw5WnN9wqXW7krUkUowxOI1wr?usp=sharing
2. Open Tlauncher (make sure tLauncher is updated to the latest version) and then open the TLmods tab
3. Click the screwdriver and wrench icon on the upper left part of the window, and select `Backup Mods`.
4. Find the mod zip file you downloaded and click restore.
5. Launch the game.
### 1.2 Joining VPN
1. Install Tailscale from https://tailscale.com/download
2. Open Tailscale and log in using the account provided by JP.
3. Verify using phone number if prompted.
4. Wait for OTP and authorization.
5. Confirm your device is listed as connected.
#### Notes: 
- Each device must have the VPN client installed (If using VPN)
- Up to 3 accounts can connect to a network in Tailscale, so inform JP if using your own account.
### 1.3 Connecting to Server
1. On Minecraft, join the server using the url 'servera'
2. If the server is still off, go to http://servera/ and press the start button to  start the server.
## 2 Server Setup
Steps required to develop, deploy, expose, and operate a server.
### 2.1 Ubuntu Server Installation
Skip to [2.5](#25-deploy-server-ubuntu-cli) If Ubuntu is already installed.

1. Prepare:
    - Blank drive on target PC.
    - 8GB+ USB drive.
    - Ubuntu Server ISO downloaded.
    - Flashing tool ready (Rufus, Balena Etcher, etc.).
2. Flash Ubuntu Server ISO to USB using your chosen tool.
3. Insert USB into target PC.
4. Boot from USB (adjust BIOS boot order if necessary).
5. Follow installation prompts:
    - Select language and keyboard layout.
    - Accept all default options.
    - Do not install additional packages.
    - Create admin user (example: jp) and set a password.
6. Complete installation and reboot into Ubuntu Server.
### 2.2 Deploy Vanilla Server
1. Install Java JDK from here: https://www.oracle.com/java/technologies/downloads/
    - Recommended version 21
2. Download the Minecraft server jar. 1.21.1 available here: 
    - https://piston-data.mojang.com/v1/objects/6bce4ef400e4efaa63a13d5e6f6b500be969ef81/server.jar
    - https://www.minecraft.net/en-us/download/server
3. Make a folder where you will place the server. Move the jar file to that folder.
4. Open cmd in that folder.
5. Run: 
    ```shell
    java -jar server.jar. 
    ```
    After running, it will generate some files and folders. Including `eula.txt`
6. Open `eula.txt` and find the line that says `eula=false`. Set it to true.
7. To start the server, run: 
    ```shell
    java -jar server.jar --nogui, 
    ```
    Or if you want to set the start and max ram usage: 
    ```shell
    java -Xms1024M -Xmx2048M -jar server.jar --nogui
    ```
### 2.3 Create and Distribute Modpacks
1. Open TLauncher and navigate to TLMods.
2. Select the modpack of your choice or  download your own set of mods.
3. Test run by launching the game.
4. Do necessary tweaks and optimizations
5. Export the modpack by clicking the screwdriver & wrench icon and then select Backup Mods.
6. Select Create Backup, choose the modpack, and then the save location.
7. Share the newly created zip file.
### 2.4 Deploy Modded Server (Windows)
Preconditions:
- Modpack created and ready for distribution (See [2.3](#23-create-and-distribute-modpacks)).
- Admin machine has sufficient resources for server.

Steps:
1. Install Java JDK from https://www.oracle.com/java/technologies/downloads/
    - Recommended version 17 for modded
2. Create a folder for the server files (e.g., C:\MinecraftServer).
3. Download server Forge installer matching Minecraft version from https://files.minecraftforge.net/net/minecraftforge/forge/
4. Run the Forge installer by double clicking it. Select `Server` → browse to server folder.
5. Run `run.bat` once to generate default files.
6. Accept EULA by opening `eula.txt` then set `eula=true`.
7. Copy modpack files to server folder:
    - `mods/`, `config/`, `defaultconfigs/`, `scripts/`, `kubejs/`
    - Remove client-only mods (Oculus, LegendaryTooltips).
8. Adjust server startup script (optional):
    - Set RAM allocation: `-Xms1024M -Xmx2048M`.
9. Start server by double-clicking run.bat

Notes:
- Eula.txt will only show up if installation is successful.
- If the server starts successfully, you should see a window with a graph and console.
- The arguments when starting the server, -Xms1024M and -Xmx2048M, set the max ram usage at startup and at runtime. If not set, the default value is 1/16 and 1/4 of the installed ram, respectively.
- During installation, run the Forge jar by DOUBLE-CLICKING, NOT CMD. Using CMD will install to the default location: `C:\Users\<YourUsername>\AppData\Roaming\.minecraft`
- I said to copy just those folders in step 6. I did that + copied everything else in the tlauncher folder and it still worked fine. Just need to delete client side mods(Oculus and Legendarytooltips) from the mod folder.
- If run.bat shows a terminal window running and then it outputs an error after loading the mods, you can see the cause on the first few lines of error after the list of mods (oculus error or something). Usually just means you need to delete client-side mods.
### 2.5 Deploy Server (Ubuntu CLI)
Preconditions:
- Modpack created and ready for distribution (See [2.3](#23-create-and-distribute-modpacks)).
- Admin machine has sufficient resources for server.
- Ubuntu Server is installed and booted

Steps:
1. Create a folder for the server files using:
    ```shell
    mkdir MinecraftFolder
    ```
2. Run these commands to install Samba
    ```shell
    sudo apt update
    sudo apt install samba
    ```
3. Run the command to open the Samba config using Nano text editor:
    ```shell
    sudo nano /etc/samba/smb.conf
    ```
4. At the end of the file, append:
    ```ini
    [global] 
    workgroup = WORKGROUP 
    security = user 
    map to guest = never 
    server string = Samba Server 
    passdb backend = tdbsam 
    
    # Force modern authentication 
    client min protocol = SMB2 
    server min protocol = SMB2 
    ntlm auth = yes 
    
    [MinecraftFolder] 
    path = /home/jp/MinecraftFolder
    browseable = yes 
    read only = no 
    guest ok = no 
    valid users = jpazo
    ```
5. Press ctrl+o to save, ctrl+x to close Nano
6. Create an SMB password using this command:
    ```shell
    sudo smbpasswd -a jp
    ```
7. Restart smb using:
    ```shell
    sudo systemctl restart smbd nmbd
    ```
8. Run this command to install Tailscale.
    ```shell
    curl -fsSL https://tailscale.com/install.sh | sh
    ```
9. Join the tailscale network using the command:
    ```shell
    sudo tailscale up
    ```
10.  The previous command should give a url. Open it and authorize to join the network.
11. Install JDK using these commands
    ```shell
    sudo apt update
    sudo apt install openjdk-17-jdk
    ```
10. On Windows Explorer, where you created the modpack, type `\\ServerName` in the address bar to access the server.
11. Type in the username and password.
12. Open the shared folder and copy the files created using section [2.3](#23-create-and-distribute-modpacks).
13. After the file copy is finished, run the following:
    ```shell
    cd MinecraftFolder
    chmod a+x run.sh
    ./run.sh
    ```
### 2.6 Server Properties and Other Configs
1. Open server.properties
   - Navigate to the server folder
   - Open `server.properties` in a text editor
2. Set server identity and world
   - Server name (displayed in server list): `motd=Totally Chill Server`
   - World name (Also folder containing world files): `level-name=myworld`
   - World seed (applies only on initial world generation): `level-seed=69420018030897796`
        - Seed to na sinend ni Den - village sa baba surrounded by cherry blossoms)
3. Set player limits
   - Maximum number of players: `max-players=20`
4. Account and validation settings
   - Allow cracked/unofficial accounts: `online-mode=false`
   - Disable Mojang account validation to prevent registry sync fail: `enforce-secure-profile=false`
5. Optional gameplay and server settings
   - Adjust difficulty, game mode, PvP, spawn protection, and other options as desired
   - Enable whitelist (note: may not be fully effective if online-mode and enforce-secure-profile are false)
6. Save and close the file
   - Stop the server before saving changes
   - Keep a backup of `server.properties` for recovery
7. Verify settings
   - Restart the server
   - Confirm world loads with correct name/seed
   - Check that max players, MOTD, and account settings are applied correctly
   - Observe console for errors or warnings related to properties

Notes
   - Configs are the same for Windows and Ubuntu servers
   - Most admin functions can be done without OP privileges
   - Any additional mods or plugin configs should go in their respective folders (`mods/`, `config/`, `scripts/`) and be verified
   - Regularly backup server properties and world files
   - Enable whitelist; may not be fully effective since Mojang Account validation is disabled.
### 2.7 Network Setup (VPN Path)
Choose only 1 VPN
#### A. ZeroTier (Based on https://docs.zerotier.com/start)
1. Register a new account at https://www.zerotier.com/
2. Make a new network and open it
3. Set the ip address range to 192.168.192.xx
4. Download and install https://www.zerotier.com/download/
5. Open ZeroTier from the tray
6. Press join network and use the key provided by JP
7. Refresh your network view to see your new device.
8. Click the arrow-up icon on the left of the new device.
9. Set the device name, ip address of your choice (Within the range you chose), and check Authorize.
#### B. Radmin
1. Install Radmin from https://www.radmin-vpn.com/
2. Open Radmin and navigate to Network>Create Network
3. Input your settings
4. Distribute the network key
#### C. Tailscale
1. Go to https://tailscale.com/download and install Tailscale
2. Log in to Tailscale using the account provided by JP
3. Verify using phone number
4. Wait for the otp.
#### Notes:
- VPN essentially simulates having everyone in the same house/ LAN
- Each device must have the VPN Client installed and authorized by the admin (JP)
- Up to 10 authorized devices (including the server) can access Zerotier
- Up to 3 accounts can connect to a network in Tailscale
- Static IP addresses are used for all these VPNs
- Can be used for other purposes other than Minecraft.
### 2.8 Network Setup (Tunnel Path)
Choose only 1 Tunnel
##### A. Playit.gg
1. Register a new account at Playit.gg
2. Download and install Playit.gg app. Just follow the on-screen instructions (After installation, run it, and it will prompt you to follow a link)
3. Press Continue to add the server as an agent.
4. Rename your agent to something easy to remember. Press Add Agent.
5. Press Create Tunnel
6. On the region dropdown, select Global. Tunnel type is Minecraft Java. Ensure enable 
7. Assign the agent to a tunnel.
8. Once assigned, it will generate a url that you can share.
#### B. Ngrok
1. Refer to the instructions in https://ngrok.com/
#### Notes:
- Tunnels essentially turn you into a public server without using port forwarding.
- Only the server needs to be set up when using tunnels
- Anyone with the server url can access - less secure, DO NOT SHARE URL PUBLICLY
- Playit.gg has a randomly generated but persistent domain name/ url
- Double check ip addresses and port numbers. Make sure that Playit.gg has updated ports.
### 2.9 Server Moderation
- Refer to the Mojang website for server admin commands
- Enable whitelist; may not be fully effective since Mojang Account validation is disabled.
- Do not give op privileges to anyone unless absolutely necessary. Most admin functions can be done without op privilege.
### 2.10 Backup and Sync (WIP uwu)
1. Install Google Drive Desktop
2. Log in using the account provided by JP
3. Run backup.bat to backup the world files to Google Drive.

-File Sync between and backup system is still in development. Use  Google Drive in the meantime.
## 3 General Notes and Known Issues
Common, observed problems and their usual causes.
### 3.1 Tunnels
- Playit.gg has a glitch that causes connections to fail even with proper settings. Workaround is either set the port to something else and then set it back to the proper port, or unassign all agents, deactivate all tunnels, then reactivate 1 tunnel and reassign your agent.
- When the server is laggy, the only solution so far is switch between different services or swap servers.
- Ngrok is fast but has a **1GB** transfer limit **PER MONTH**
### 3.2 Ubuntu Server Installation
- The installation may fail during the kernel installation phase. Force shutdown and  repeat the installation in case this happens. Cause is still unknown.
### 3.3 Software Versions
#### 3.3.1 Forge
- Latest supported JDK version is 17 for modded and 21 for Vanilla. 24 needs to be tested per mod. If bugs appear, use the older JDK versions.
- Use the Forge version for the Minecraft version of the modpack (Minecraft version indicated at the tlauncher modpack download page). Use the recommended versions of Forge, not the latest, unless you have a very specific reason.
### 3.4 Security
- The tutorial assumes all account credentials are distributed privately. 
- Whitelist is not very effective if online-mode and enforce-secure-profile in server.properties are both false. This is because anyone can just change usernames, and it won't be verified by the server.
- VPN is more secure because it is invite only and admin has full control of network
- Tunnels, while technically public, can  be used as backup in case of performance issue with VPNs
- Unverified accounts are accepted. Breaches are handled on case by case basis.
- A proper backup system is still under development.
### 3.5 Server Admin Quality of Life Tips
- Make your own batch files so you don't have to open the terminal and type commands each time you start the server. Forge does this for you, but you can still edit the run.bat or run.sh files.
- I suggest to run both the VPN and the Minecraft server as daemons in Ubuntu Server

## 4 Advanced Ubuntu Server Setup
### 4.1 Automatic Boot Using Systemd Service
This guide explains how to move a modded Minecraft server to `/opt/minecraft` and configure it to start automatically at boot using `systemd`.

Steps:
1. Move Server Files to `/opt`
    Create the target directory:
    ```shell
    sudo mkdir -p /opt/minecraft
    ```
    Move your existing server files:
    ```shell
    sudo mv /home/marites/Minecraft/* /opt/minecraft/
    ```
2. Set Ownership to the Service User
    Assuming you created a user named `server`:
    ```shell
    sudo chown -R server:server /opt/minecraft
    ```
    Verify ownership:
    ```shell
    ls -ld /opt/minecraft
    ```
    The owner and group should both be `server`.
3. Make `run.sh` Executable
    ```shell
    cd /opt/minecraft
    chmod +x run.sh
    ```
    Ensure `run.sh` launches the server directly (no `screen`, no interactive prompts).
4. Create the systemd Service File
    Create the service definition:
    ```shell
    sudo nano /etc/systemd/system/minecraft.service
    ```
    Paste the following:
    ```ini
    [Unit]
    Description=Modded Minecraft Server
    After=network.target

    [Service]
    User=server
    WorkingDirectory=/opt/minecraft
    ExecStart=/bin/bash /opt/minecraft/run.sh
    Restart=always
    RestartSec=10

    [Install]
    WantedBy=multi-user.target
    ```
    Save and exit.
5. Reload systemd
    ```shell
    sudo systemctl daemon-reload
    ```
6. Enable Automatic Startup
    ```shell
    sudo systemctl enable minecraft
    ```
    This ensures the server starts automatically at boot.
7. Start the Server Now
    ```shell
    sudo systemctl start minecraft
    ```
8. Check Status
    ```shell
    sudo systemctl status minecraft
    ```
    If the service is active (running), it is working correctly.
    To view live logs:
    ```shell
    sudo journalctl -u minecraft -f
    ```
9. Test Boot Persistence
    Reboot the machine:
    ```shell
    sudo reboot
    ```
    After the system comes back online:
    ```shell
    sudo systemctl status minecraft
    ```
    If it is running, the setup is complete.

Notes:
- Ensure Java is installed system-wide:
    ```shell
    java -version
    ```
- If needed, verify Java works for the `server` user:
    ```shell
    sudo -u server java -version
    ```
- The service will automatically restart if it crashes due to `Restart=always`.

The server is now managed by the operating system and will run in the background without requiring login or manual execution.

### 4.2 Live Backup Guide
This guide explains how to safely back up a running Minecraft server's world files as zip archives without shutting down the server. Backups are stored in `/home/marites/backups/` and can be automated.

Steps:
1. Install Required Tools
    Make sure `zip` and `mcrcon` are installed:
    ```bash
    sudo apt update
    sudo apt install zip mcrcon
    ```
2. Enable RCON in Minecraft
    Edit `server.properties` and set:
    ```ini
    enable-rcon=true
    rcon.password=YourStrongPasswordHere
    rcon.port=25575
    ```
    Restart the server for changes to take effect.
3. Create Backup Script
    Create the backup script:
    ```bash
    sudo nano /opt/minecraft/backup.sh
    ```
    Paste the following (update the RCON password and directories):
    ```bash
    #!/bin/bash

    RCON_PASS="<YourStrongPasswordHere>"
    WORLD_DIR="/opt/Minecraft/The Server"
    BACKUP_DIR="/home/marites/backups"
    DATE=$(date +"%Y-%m-%d_%H-%M-%S")

    mkdir -p "$BACKUP_DIR"

    # Notify players that backup is starting
    mcrcon -H 127.0.0.1 -P 25575 -p "$RCON_PASS" "say Backup starting! Server may lag for a few seconds."

    # Force world save and disable writes
    mcrcon -H 127.0.0.1 -P 25575 -p "$RCON_PASS" "save-all"
    mcrcon -H 127.0.0.1 -P 25575 -p "$RCON_PASS" "save-off"

    # Give disk a moment to flush
    sleep 5

    # Create zip backup
    if zip -r "$BACKUP_DIR/world_$DATE.zip" "$WORLD_DIR"; then
        # Backup succeeded
        mcrcon -H 127.0.0.1 -P 25575 -p "$RCON_PASS" "say Backup completed successfully! Saved to $BACKUP_DIR/world_$DATE.zip"
    else
        # Backup failed
        mcrcon -H 127.0.0.1 -P 25575 -p "$RCON_PASS" "say Backup failed! Check server logs."
    fi

    # Re-enable saving
    mcrcon -H 127.0.0.1 -P 25575 -p "$RCON_PASS" "save-on"
    ```
    Make it executable:
    ```bash
    chmod +x /opt/minecraft/backup.sh
    ```
4. Test the Script
    Run manually:
    ```bash
    /opt/minecraft/backup.sh
    ```
    Check `/home/marites/backups/` for the new zip file.
5. Automate Backups with Cron
    Edit root's crontab:
    ```bash
    sudo crontab -e
    ```
    Add the following to run backups at midnight and noon every day:
    ```cron
    0 0,12 * * * /opt/minecraft/backup.sh >> /var/log/minecraft-backup.log 2>&1
    ```
    This also logs output to `/var/log/minecraft-backup.log`.

Notes:
- `save-all` flushes all world data to disk.
- `save-off` prevents world changes during the copy.
- `save-on` resumes automatic saving.
- Ensure the server's timezone is correct:
    ```bash
    timedatectl
    ```
- Your backup script can be combined with another script to copy zip files to a remote server.



[⬆ Back to Top](#table-of-contents)

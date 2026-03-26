import os
import socket
from flask import Flask, Response
from dotenv import load_dotenv

app = Flask(__name__)

# Load MAC address from mcweb.env
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "mcweb.env"))
MAC = os.getenv("SERVERMAC")
MAC = "48:0f:cf:4d:dd:c8"
def send_wol(mac):
    mac_bytes = bytes.fromhex(mac.replace(":", ""))
    packet = b'\xff' * 6 + mac_bytes * 16
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.sendto(packet, ('192.168.1.255', 9))  # LAN broadcast
    sock.close()


@app.route("/boot")
def boot():
    # Fire WOL only when someone connects
    if MAC:
        send_wol(MAC)

    # Inline boot animation HTML
    html = """
    <!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Booting Up...</title>
<link rel="stylesheet" href="../static/global.css"> <!-- corrected path -->
<style>
  body {
    background-color: var(--canvas-bg);
    color: var(--text);
    font-family: Arial, Helvetica, Roboto, sans-serif;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    height: 100vh;
    margin: 0;
  }

  .spinner {
    border: 6px solid var(--border);       /* visible ring */
    border-top: 6px solid var(--ui-blue);  /* accent highlight */
    border-radius: 50%;
    width: 60px;
    height: 60px;
    animation: spin 1.5s linear infinite;
    margin-bottom: 40px;
  }

  @keyframes spin {
    0%   { transform: rotate(0deg); }
    100% { transform: rotate(360deg); }
  }

  .tooltip {
    font-size: 1.2em;
    text-align: center;
    min-height: 40px;
    opacity: 0;
    transition: opacity 1s ease-in-out;
    color: var(--theme-text-soft);
  }

  .tooltip.show {
    opacity: 1;
  }
</style>
</head>
<body>
  <div class="spinner"></div>
  <div id="tooltip" class="tooltip">Booting...</div>

<script>
const messages = [
  "Preparing for battle...",
  "Hacking the pentagon...",
  "Summoning the main character...",
  "Charging the sigma energy...",
  "LEEEERROOYY JJEEENKINSS!",
  "Brewing coffee for the server...",
  "Installing chaos.exe...",
  "Polishing the rizz crystals...",
  "Not everything you see on the internet is true - Abraham Lincoln",
  "Are you sure you opened the right server?",
  "This is taking a bit too long...",
  "Better get some snacks",
  "Never gonna give you up",
  "Remember, switching to your sword is faster than reloading",
  "Too close for missiles. Switching to guns. Wait. Wrong game"
];

function shuffle(array) {
  for (let i = array.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [array[i], array[j]] = [array[j], array[i]];
  }
  return array;
}

let shuffled = shuffle([...messages]);
let index = 0;
const tooltip = document.getElementById("tooltip");

function showMessage() {
  tooltip.classList.remove("show");
  setTimeout(() => {
    tooltip.textContent = shuffled[index];
    tooltip.classList.add("show");
    index = (index + 1) % shuffled.length;
  }, 500);
}

showMessage();
setInterval(showMessage, 5000);

const prefersDark = window.matchMedia("(prefers-color-scheme: dark)");
function applyTheme(e) {
  if (e.matches) {
    document.documentElement.classList.add("theme-dark");
  } else {
    document.documentElement.classList.remove("theme-dark");
  }
}
applyTheme(prefersDark);
prefersDark.addEventListener("change", applyTheme);
</script>
</body>
</html>

    """
    return Response(html, mimetype="text/html")

if __name__ == "__main__":
    # Flask only runs when Nginx proxies to it
    app.run(host="127.0.0.1", port=5000)

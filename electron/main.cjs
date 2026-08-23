const { app, BrowserWindow, ipcMain, Menu } = require("electron");
const path = require("path");
const fs = require("fs");
const { spawn, execSync } = require("child_process");
const http = require("http");

const PORT = 47102;
const DASHBOARD_URL = `http://127.0.0.1:${PORT}/ui/`;
const HEALTH_URL = `http://127.0.0.1:${PORT}/api/health`;

let mainWindow = null;
let pythonProcess = null;
let isQuitting = false;

function getPythonExecutable(projectRoot) {
  // 1. Check local virtual environment (.venv)
  const venvWindows = path.join(projectRoot, ".venv", "Scripts", "python.exe");
  const venvUnix = path.join(projectRoot, ".venv", "bin", "python");

  if (process.platform === "win32" && fs.existsSync(venvWindows)) {
    return { command: venvWindows, args: [] };
  }
  if (process.platform !== "win32" && fs.existsSync(venvUnix)) {
    return { command: venvUnix, args: [] };
  }

  // 2. Check uv
  try {
    execSync("uv --version", { stdio: "ignore" });
    return { command: "uv", args: ["run", "python"] };
  } catch (e) {
    // uv not found
  }

  // 3. Fallback to global python
  return { command: process.platform === "win32" ? "python" : "python3", args: [] };
}

function freePortsIfBusy(ports = [47102, 9224]) {
  if (process.platform !== "win32") return;
  for (const port of ports) {
    try {
      const output = execSync(`netstat -ano | findstr :${port}`, { encoding: "utf-8" });
      const lines = output.split("\n");
      for (const line of lines) {
        const parts = line.trim().split(/\s+/);
        if (parts.length >= 5 && parts[1].includes(`:${port}`) && parts[3] === "LISTENING") {
          const pid = parts[parts.length - 1];
          if (pid && pid !== "0" && pid !== process.pid.toString()) {
            console.log(`[*] Freeing busy port ${port}: Stopping old process (PID: ${pid})...`);
            try {
              execSync(`taskkill /pid ${pid} /F /T`, { stdio: "ignore" });
            } catch (e) {}
          }
        }
      }
    } catch (e) {
      // Port already free
    }
  }
}

function startPythonBackend() {
  freePortsIfBusy([47102, 9224]);

  const projectRoot = path.resolve(__dirname, "..");
  const { command, args } = getPythonExecutable(projectRoot);

  console.log(`[*] Starting FBEM Python backend with: ${command} ${args.join(" ")}`);

  const spawnArgs = [...args, "-m", "fbem.bridge"];

  try {
    pythonProcess = spawn(command, spawnArgs, {
      cwd: projectRoot,
      env: {
        ...process.env,
        PYTHONUNBUFFERED: "1",
        PYTHONIOENCODING: "utf-8",
      },
      shell: process.platform === "win32",
    });

    pythonProcess.stdout.on("data", (data) => {
      console.log(`[FBEM-Backend] ${data.toString().trim()}`);
    });

    pythonProcess.stderr.on("data", (data) => {
      console.error(`[FBEM-Backend-ERR] ${data.toString().trim()}`);
    });

    pythonProcess.on("exit", (code, signal) => {
      console.log(`[FBEM-Backend] Process exited with code ${code}, signal ${signal}`);
      if (!isQuitting && mainWindow && !mainWindow.isDestroyed()) {
        console.warn("[FBEM-Backend] Warning: Python backend stopped unexpectedly.");
      }
    });
  } catch (err) {
    console.error("[!] Failed to spawn Python backend:", err);
  }
}

function pollHealth(url, timeoutMs = 25000) {
  return new Promise((resolve, reject) => {
    const startTime = Date.now();

    const check = () => {
      const req = http.get(url, (res) => {
        if (res.statusCode === 200) {
          resolve(true);
        } else if (Date.now() - startTime > timeoutMs) {
          reject(new Error(`Server returned status ${res.statusCode}`));
        } else {
          setTimeout(check, 400);
        }
      });

      req.on("error", () => {
        if (Date.now() - startTime > timeoutMs) {
          reject(new Error("Timeout waiting for backend server"));
        } else {
          setTimeout(check, 400);
        }
      });

      req.setTimeout(1500, () => {
        req.destroy();
        if (Date.now() - startTime > timeoutMs) {
          reject(new Error("Request timed out"));
        } else {
          setTimeout(check, 400);
        }
      });
    };

    check();
  });
}

function killPythonProcess() {
  if (!pythonProcess) return;

  const pid = pythonProcess.pid;
  console.log(`[*] Stopping Python backend process (PID: ${pid})...`);

  try {
    if (process.platform === "win32") {
      execSync(`taskkill /pid ${pid} /T /F`, { stdio: "ignore" });
    } else {
      pythonProcess.kill("SIGTERM");
    }
  } catch (e) {
    // Process might already be dead
  }
  pythonProcess = null;
}

function createMainWindow() {
  mainWindow = new BrowserWindow({
    title: "⚡ FBEM Studio — Facebook Automation",
    width: 1360,
    height: 880,
    minWidth: 1020,
    minHeight: 680,
    backgroundColor: "#0b1220",
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      webviewTag: false,
      backgroundThrottling: false,
    },
  });

  // Open external links (e.g. facebook.com) in user's default browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    require("electron").shell.openExternal(url);
    return { action: "deny" };
  });

  // Load splash loading screen first
  mainWindow.loadFile(path.join(__dirname, "splash.html"));
  mainWindow.once("ready-to-show", () => {
    mainWindow.show();
  });

  // Wait for Python backend to be ready, then navigate to dashboard
  pollHealth(HEALTH_URL)
    .then(() => {
      console.log(`[+] FBEM backend is ready! Loading dashboard at ${DASHBOARD_URL}`);
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.loadURL(DASHBOARD_URL);
      }
    })
    .catch((err) => {
      console.error("[!] Backend health check failed:", err.message);
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.loadURL(DASHBOARD_URL); // attempt load anyway
      }
    });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

// Window control IPC handlers
ipcMain.on("window-minimize", () => {
  if (mainWindow) mainWindow.minimize();
});

ipcMain.on("window-maximize", () => {
  if (mainWindow) {
    if (mainWindow.isMaximized()) {
      mainWindow.unmaximize();
    } else {
      mainWindow.maximize();
    }
  }
});

ipcMain.on("window-close", () => {
  if (mainWindow) mainWindow.close();
});

// App Lifecycle
app.whenReady().then(() => {
  startPythonBackend();
  createMainWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow();
    }
  });
});

app.on("before-quit", () => {
  isQuitting = true;
  killPythonProcess();
});

app.on("window-all-closed", () => {
  isQuitting = true;
  killPythonProcess();
  if (process.platform !== "darwin") {
    app.quit();
  }
});

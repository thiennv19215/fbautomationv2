const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("fbemDesktop", {
  platform: process.platform,
  version: process.env.npm_package_version || "1.0.0",
  isElectron: true,
  minimize: () => ipcRenderer.send("window-minimize"),
  maximize: () => ipcRenderer.send("window-maximize"),
  close: () => ipcRenderer.send("window-close"),
});

// 桌面应用入口：供 electron-builder 打包时使用，开发时仍用 electron/main.js
const path = require('path')
require(path.join(__dirname, 'electron', 'main.js'))

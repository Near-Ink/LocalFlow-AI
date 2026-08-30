# LocalFlow AI Desktop

Electron 桌面端骨架。

## 开发

```bash
cd desktop
npm install
npm run dev    # 开发模式（带 DevTools）
npm start      # 正常启动
```

## 打包

```bash
npm run build:mac      # macOS
npm run build:win      # Windows
npm run build:linux    # Linux
```

## 架构

```
desktop/
├── src/
│   ├── main/          # 主进程
│   │   ├── main.js        窗口管理 / 生命周期 / IPC
│   │   └── preload.js     安全桥接
│   └── renderer/      # 渲染进程（前端 UI）
│       └── index.html     初始骨架页
└── package.json
```

MVP 阶段渲染层可用原生 HTML + JS 快速迭代，后续可替换为 React/Vue。
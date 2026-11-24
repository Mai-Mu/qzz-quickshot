# 瞬截 · qzz-quickshot

瞬截是面向 Windows 11 x64 的托盘截图工具。按快捷键框选屏幕，可直接批注、取色、扫码、保存或原位置顶贴图。截图和识别都在本机完成，Windows 解压包内含离线 OCR 引擎和简体中文、英文模型。

## 下载与启动

1. 从最新发布下载 `qzz-quickshot-v<版本>-windows-x64.zip`。[查看 GitHub Releases](https://github.com/Mai-Mu/qzz-quickshot/releases/latest)
2. 完整解压，打开 `qzz-quickshot` 文件夹中的 `瞬截.exe`。移动程序时请保留整个文件夹。
3. 按 `Ctrl+Shift+F2` 开始截图。右键点击任务栏通知区域的瞬截图标，可以开始截图、改快捷键或查看版本。首次启动时，Windows 通知会提示程序在托盘运行。图标折叠时，可在托盘的隐藏图标中找到。

升级时，先从托盘退出当前版本，再解压并运行新版。

## 截图与批注

| 操作 | 用法 |
| --- | --- |
| 框选和调整 | 在鼠标所在的显示器上拖动框选。松手后可通过八个控制点调整大小，并在选区旁查看实际像素尺寸。 |
| 移动或取消选区 | 按住选区内部拖动，选区和已有批注一同移动。已启用批注工具时，可再次点击该工具的图标退出批注模式。右键清除选区，再右键退出截图。 |
| 放大取色 | 框选或拉动控制点时显示像素放大器。按 `Shift` 切换 RGB 和 HEX，按 `C` 复制色值。选区就绪后，按住 `Alt` 可再次查看。 |
| 绘制和编辑批注 | 支持矩形、椭圆、箭头、线条、画笔、荧光笔、马赛克、模糊和文字。选中对应工具可编辑已有的同类批注。矩形和椭圆等同一工具的样式共用颜色、粗细。 |
| 马赛克和擦除 | 马赛克支持矩形和圆笔涂抹。橡皮擦可以局部清除批注。按 `Ctrl+Z` 撤销、`Ctrl+Y` 或 `Ctrl+Shift+Z` 重做。 |
| 扫描选区二维码 | 点击选区右上侧的二维码按钮。识别成功后，原始内容直接复制到剪贴板。 |
| 复制、保存、贴图 | 按 `Enter` 复制图片、`Ctrl+S` 保存 PNG、`P` 原位贴图。也可使用工具栏按钮，导出的图片保留实际像素尺寸和批注。 |

## 贴图与离线识别

贴图按选区原来的位置和大小置顶显示，可以同时保留多张。贴图显示后，瞬截在后台识别简体中文、英文和二维码。

- 像选网页文字一样用鼠标拖选，按 `Ctrl+C` 复制选中的文字。右键菜单也可复制图片。
- 识别到二维码后，右键选择「复制二维码内容」。
- 从图片空白处拖动可移动贴图，按住 `Alt` 可从图片任意位置拖动。按 `Esc` 或从右键菜单关闭当前贴图。

## 从源码运行

使用 Windows 11 x64 和 Python 3.12 x64。获取源码后，在项目根目录执行后续命令。

```powershell
git clone https://github.com/Mai-Mu/qzz-quickshot.git
cd qzz-quickshot
```

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File .\scripts\prepare_ocr.ps1
.\.venv\Scripts\python.exe .\run_quickshot.py
```

首次准备依赖需要联网。`prepare_ocr.ps1` 下载并校验 OCR 引擎、DLL 和语言模型，资源准备好后即可离线运行。

## 构建 Windows 解压包

在已创建 `.venv` 的项目根目录执行以下命令。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
```

构建脚本会准备 OCR 依赖、生成并校验两个压缩包。每次成功构建后，`quickshot/__init__.py` 中的版本号末位自动递增。设置页、EXE 文件属性和压缩包使用同一个版本号，既有版本的压缩包会保留在 `dist/`。

| 构建产物 | 内容 |
| --- | --- |
| `dist/qzz-quickshot-v<版本>-windows-x64.zip` | 可解压运行的 Windows x64 程序及离线识别资源 |
| `dist/qzz-quickshot-v<版本>-source.zip` | 对应版本的源码和构建脚本 |

第三方组件的来源和许可见项目声明，Windows 发布包也附有许可证文本。[第三方组件声明](THIRD_PARTY_NOTICES.md)

## 项目目录

| 路径 | 用途 |
| --- | --- |
| `run_quickshot.py` | 应用入口 |
| `quickshot/` | 托盘、全局快捷键、截图、批注、贴图和识别 |
| `quickshot/assets/` | 应用图标 |
| `scripts/` | OCR 资源准备、Windows 打包和第三方声明收集 |
| `requirements.txt`、`requirements-build.txt` | 运行和构建依赖 |
| `ocr_runtime/` | 准备脚本下载的离线 OCR 资源 |
| `build/`、`dist/` | 本地构建缓存和发布包 |

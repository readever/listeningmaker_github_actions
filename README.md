# ListeningMaker desktop build

英语听力学语音生成工具的桌面版打包工程。框架依赖 PyPI 的 simplestart（Cython 编译保护版），
本仓库只包含应用层文件，不含任何 simplestart 源码。

## 文件说明

| 文件 | 作用 |
|---|---|
| app.py | 应用主体（simplestart 界面 + TTS 逻辑） |
| launcher.py | 打包入口：起本地服务 + pywebview 原生窗口，数据目录按平台区分 |
| modelscope_patch.py | 模型下载重定向到 ModelScope；优先读包内 models/（离线） |
| voices/*.wav | 内置克隆音色样本（播音/口语 × 男女，打包进 app，缺文件则选项自动隐藏） |
| list_submodules.py | 枚举 simplestart 的 .so/.pyd 子模块（PyInstaller 需显式 hidden-import） |
| build.sh | macOS 构建脚本（产出 dist/ListeningMaker.app） |
| build_windows.ps1 | Windows 构建脚本（产出 dist/ListeningMaker/） |
| warm_models.py | 预下载 TTS 模型到 ~/.cache/pocket-tts-modelscope（打包时内置） |
| .github/workflows/release.yml | tag 触发的双平台构建，产物挂到 GitHub Release |

## 本地构建（macOS）

```bash
conda create -n ssbuild python=3.10 -y
conda activate ssbuild
pip install simplestart==0.0.1.66 pocket_tts pywebview pyinstaller
python warm_models.py      # 可选，内置模型免联网（~335MB）
bash build.sh
```

## 发布流程

1. 打 tag 并推送：`git tag v0.0.1.66 && git push origin v0.0.1.66`
2. GitHub Actions 自动构建 mac dmg + windows zip
3. 产物自动挂到该 tag 的 GitHub Release
4. 从 Release 下载安装包，转存到 ModelScope 模型仓库对外分发

## 注意事项

- 构建环境必须是干净的 PyPI 安装（simplestart .so 保护版），不要 `pip install -e` 本地源码
- PyInstaller 无法扫描 .so 内部的 import：simplestart 的纯 Python 依赖已在构建脚本里
  collect-all 显式声明，新增依赖时记得同步两份构建脚本
- 应用数据（outputs/uploads）位置：macOS `~/Library/Application Support/ListeningMaker`，
  Windows `%APPDATA%\ListeningMaker`

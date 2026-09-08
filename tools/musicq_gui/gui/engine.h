#pragma once

#include <QString>
#include <QStringList>

// musicq 引擎调用解析：
// 发布态用程序同目录 engine/musicq-engine.exe（PyInstaller 冻结产物），
// 开发态回退 tools/musicq/.venv 下的 python -m musicq。
struct EngineSpec {
    QString program;        // 可执行程序路径
    QStringList prefixArgs; // python 回退时为 ["-m", "musicq"]
    QString workDir;        // 工作目录（python -m 需要包所在目录）
    QString describe;       // 状态栏展示用描述
};

// 解析引擎；成功返回 true，失败返回 false 并填 err（中文提示）
bool resolveEngine(EngineSpec& spec, QString& err);

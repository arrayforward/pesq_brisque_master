#include "engine.h"

#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>

bool resolveEngine(EngineSpec& spec, QString& err) {
    const QString appDir = QCoreApplication::applicationDirPath();

    // 1) 发布态：<exe>/engine/musicq-engine.exe
    const QString frozen = appDir + "/engine/musicq-engine.exe";
    if (QFileInfo::exists(frozen)) {
        spec = {frozen, {}, appDir, QStringLiteral("冻结引擎: %1").arg(frozen)};
        return true;
    }

    // 2) 开发态：<repo>/tools/musicq/.venv（exe 位于 tools/musicq_gui/build/<cfg>）
    QDir d(appDir);
    if (d.cdUp() && d.cdUp() && d.cdUp()) {  // -> tools/
        const QString py = d.filePath("musicq/.venv/Scripts/python.exe");
        if (QFileInfo::exists(py)) {
            spec = {py, {"-m", "musicq"}, d.filePath("musicq"),
                    QStringLiteral("开发态 Python: %1").arg(py)};
            return true;
        }
    }

    err = QStringLiteral(
        "找不到 musicq 引擎。\n"
        "发布包应有 <程序目录>/engine/musicq-engine.exe；\n"
        "开发态应存在 tools/musicq/.venv（先按 tools/musicq/README.md 建环境）。");
    return false;
}

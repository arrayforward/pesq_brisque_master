#include "main_window.h"

#include "capture_tab.h"
#include "engine.h"
#include "eval_tab.h"
#include "gen_tab.h"

#include <QStatusBar>
#include <QTabWidget>

MainWindow::MainWindow(QWidget* parent) : QMainWindow(parent) {
    setWindowTitle(tr("musicq 音质评估工具"));
    resize(1280, 860);
    tabs_ = new QTabWidget(this);
    auto* genTab = new GenTab(this);
    captureTab_ = new CaptureTab(this);
    evalTab_ = new EvalTab(this);
    tabs_->addTab(genTab, tr("音源生成"));
    tabs_->addTab(captureTab_, tr("采集"));
    tabs_->addTab(evalTab_, tr("评估"));
    setCentralWidget(tabs_);

    // 采集完成 → wav 填入评估 Tab 并跳转
    connect(captureTab_, &CaptureTab::captured, this, [this](const QString& wav) {
        evalTab_->setWav(wav);
        tabs_->setCurrentWidget(evalTab_);
    });

    // 状态栏显示引擎解析结果
    EngineSpec spec;
    QString err;
    if (resolveEngine(spec, err))
        statusBar()->showMessage(spec.describe);
    else
        statusBar()->showMessage(err);
}

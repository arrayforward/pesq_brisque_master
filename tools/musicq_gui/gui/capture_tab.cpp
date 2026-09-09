#include "capture_tab.h"

#include "engine.h"
#include "proc_runner.h"

#include <QComboBox>
#include <QCoreApplication>
#include <QDateTime>
#include <QDir>
#include <QFileDialog>
#include <QFileInfo>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QLabel>
#include <QLineEdit>
#include <QMessageBox>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QRegularExpression>
#include <QSpinBox>
#include <QStandardPaths>
#include <QVBoxLayout>

CaptureTab::CaptureTab(QWidget* parent)
    : QWidget(parent),
      proc_(new ProcRunner(this)),
      extractProc_(new ProcRunner(this)),
      btProc_(new ProcRunner(this)) {
    auto* layout = new QVBoxLayout(this);

    // ---- 采集方式 ----
    auto* methodRow = new QHBoxLayout;
    methodCombo_ = new QComboBox;
    methodCombo_->addItem(tr("scrcpy（USB 数字回采）"));
    methodCombo_->addItem(tr("蓝牙 A2DP（PC 模拟音响）"));
    btDurSpin_ = new QSpinBox;
    btDurSpin_->setRange(10, 86400);
    btDurSpin_->setValue(300);
    btDurSpin_->setSuffix(tr(" 秒"));
    methodRow->addWidget(new QLabel(tr("采集方式:")));
    methodRow->addWidget(methodCombo_, 1);
    methodRow->addWidget(new QLabel(tr("A2DP 录制时长:")));
    methodRow->addWidget(btDurSpin_);
    layout->addLayout(methodRow);

    guideLabel_ = new QLabel;
    guideLabel_->setWordWrap(true);
    guideLabel_->setStyleSheet("color:#8ab4f8;");
    layout->addWidget(guideLabel_);

    // ---- 工具路径 ----
    auto* toolGroup = new QGroupBox(tr("工具路径（默认 PATH 查找，可改为完整路径）"));
    auto* toolForm = new QVBoxLayout(toolGroup);
    auto mkRow = [&](const QString& label, QLineEdit*& edit, const QString& def) {
        auto* row = new QHBoxLayout;
        edit = new QLineEdit(def);
        row->addWidget(new QLabel(label));
        row->addWidget(edit, 1);
        toolForm->addLayout(row);
    };
    mkRow(tr("scrcpy:"), scrcpyEdit_, QStringLiteral("scrcpy"));
    mkRow(tr("ffmpeg:"), ffmpegEdit_, QStringLiteral("ffmpeg"));
    mkRow(tr("adb:"), adbEdit_, QStringLiteral("adb"));
    layout->addWidget(toolGroup);

    // 内置工具：<exe同级>/tools/ 下存在则作为默认（用户仍可手动改）。
    // toolEnv() 已会把完整路径所在目录前置到子进程 PATH，
    // 因此内置 scrcpy 自带的 adb.exe 对 scrcpy 子进程同样可见（无需设 ADB 变量）。
    const QString appDir = QCoreApplication::applicationDirPath();
    const QString bundledFfmpeg = appDir + QStringLiteral("/tools/ffmpeg/ffmpeg.exe");
    if (QFileInfo::exists(bundledFfmpeg))
        ffmpegEdit_->setText(bundledFfmpeg);
    const QString bundledScrcpy = appDir + QStringLiteral("/tools/scrcpy/scrcpy.exe");
    if (QFileInfo::exists(bundledScrcpy))
        scrcpyEdit_->setText(bundledScrcpy);
    const QString bundledAdb = appDir + QStringLiteral("/tools/scrcpy/adb.exe");
    if (QFileInfo::exists(bundledAdb))
        adbEdit_->setText(bundledAdb);

    // ---- 设备 ----
    auto* devRow = new QHBoxLayout;
    deviceCombo_ = new QComboBox;
    deviceCombo_->setEditable(true);
    auto* refreshBtn = new QPushButton(tr("刷新设备"));
    devRow->addWidget(new QLabel(tr("设备:")));
    devRow->addWidget(deviceCombo_, 1);
    devRow->addWidget(refreshBtn);
    layout->addLayout(devRow);

    // ---- 输出 ----
    auto* outRow = new QHBoxLayout;
    outEdit_ = new QLineEdit(defaultOutDir());
    auto* outBtn = new QPushButton(tr("浏览…"));
    outRow->addWidget(new QLabel(tr("输出目录:")));
    outRow->addWidget(outEdit_, 1);
    outRow->addWidget(outBtn);
    layout->addLayout(outRow);

    // ---- 控制 ----
    auto* ctrlRow = new QHBoxLayout;
    startBtn_ = new QPushButton(tr("开始采集（请先在设备上起播）"));
    stopBtn_ = new QPushButton(tr("停止并抽取"));
    stopBtn_->setEnabled(false);
    ctrlRow->addWidget(startBtn_);
    ctrlRow->addWidget(stopBtn_);
    layout->addLayout(ctrlRow);

    statusLabel_ = new QLabel(tr("空闲"));
    layout->addWidget(statusLabel_);

    log_ = new QPlainTextEdit;
    log_->setReadOnly(true);
    log_->setMaximumBlockCount(5000);
    layout->addWidget(log_, 1);

    connect(refreshBtn, &QPushButton::clicked, this, &CaptureTab::refreshDevices);
    connect(outBtn, &QPushButton::clicked, this, &CaptureTab::browseOut);
    connect(startBtn_, &QPushButton::clicked, this, &CaptureTab::startCapture);
    connect(stopBtn_, &QPushButton::clicked, this, &CaptureTab::stopCapture);
    connect(proc_, &ProcRunner::line, this, [this](const QString& l) { appendLog("info", l); });
    connect(proc_, &ProcRunner::finishedOk, this, &CaptureTab::onRecordFinished);
    connect(proc_, &ProcRunner::failedToStart, this, [this](const QString& m) {
        capturing_ = false;
        startBtn_->setEnabled(true);
        stopBtn_->setEnabled(false);
        statusLabel_->setText(tr("scrcpy 启动失败"));
        appendLog("error", tr("scrcpy 启动失败: %1\n若未安装: winget install scrcpy").arg(m));
    });
    connect(extractProc_, &ProcRunner::line, this, [this](const QString& l) {
        appendLog(l.contains(QStringLiteral("警告")) ? "warn" : "info", l);
    });
    connect(extractProc_, &ProcRunner::finishedOk, this, &CaptureTab::onExtractFinished);
    connect(extractProc_, &ProcRunner::failedToStart, this, [this](const QString& m) {
        statusLabel_->setText(tr("引擎启动失败"));
        appendLog("error", m);
    });
    connect(btProc_, &ProcRunner::line, this, [this](const QString& l) {
        appendLog(l.contains(QStringLiteral("警告")) || l.contains(QStringLiteral("错误"))
                      ? "warn" : "info", l);
    });
    connect(btProc_, &ProcRunner::finishedOk, this, &CaptureTab::onBtFinished);
    connect(btProc_, &ProcRunner::failedToStart, this, [this](const QString& m) {
        capturing_ = false;
        startBtn_->setEnabled(true);
        stopBtn_->setEnabled(false);
        statusLabel_->setText(tr("引擎启动失败"));
        appendLog("error", m);
    });
    connect(methodCombo_, &QComboBox::currentIndexChanged, this, [this](int i) {
        const bool bt = (i == 1);
        btDurSpin_->setEnabled(bt);
        deviceCombo_->setEnabled(!bt);
        guideLabel_->setText(bt
            ? tr("A2DP 流程：① 手机「设置→蓝牙」连接本电脑（本工具启动录制后 PC 才对外呈现为蓝牙音响）"
                 "② 手机上开始播放 ③ 点「开始采集」\n"
                 "注意：A2DP 含蓝牙编解码损耗（通常 SBC），与 scrcpy 数字回采不是同一链路，分数不可跨通道比。")
            : tr("scrcpy 流程：adb 连接设备 → 设备上起播 →「开始采集」→ 播完点「停止并抽取」"));
    });
    methodCombo_->setCurrentIndex(0);
    methodCombo_->currentIndexChanged(0);

    refreshDevices();
}

QString CaptureTab::defaultOutDir() const {
    QString base = QStandardPaths::writableLocation(QStandardPaths::DocumentsLocation);
    if (base.isEmpty()) base = QDir::currentPath();
    return base + "/musicq_captures/" + QDateTime::currentDateTime().toString("yyyyMMdd_hhmmss");
}

void CaptureTab::appendLog(const QString& level, const QString& msg) {
    const QString ts = QDateTime::currentDateTime().toString("hh:mm:ss");
    QString color = "#ccc";
    if (level == "warn") color = "#e6b800";
    else if (level == "error") color = "#ff5555";
    log_->appendHtml(QString("<span style='color:#888'>[%1]</span> <span style='color:%2'>%3</span>")
                         .arg(ts, color, msg.toHtmlEscaped()));
}

QProcessEnvironment CaptureTab::toolEnv() const {
    // 把 scrcpy/ffmpeg 完整路径的所在目录前置到子进程 PATH
    QProcessEnvironment env;
    QString path = QProcessEnvironment::systemEnvironment().value("PATH");
    for (const QLineEdit* edit : {scrcpyEdit_, ffmpegEdit_, adbEdit_}) {
        const QString p = edit->text().trimmed();
        if (p.contains('/') || p.contains('\\'))
            path = QFileInfo(p).absolutePath() + ";" + path;
    }
    env.insert("PATH", path);
    return env;
}

void CaptureTab::browseOut() {
    const QString d = QFileDialog::getExistingDirectory(this, tr("选择输出目录"),
                                                        outEdit_->text());
    if (!d.isEmpty()) outEdit_->setText(d);
}

void CaptureTab::refreshDevices() {
    deviceCombo_->clear();
    QProcess p;
    p.start(adbEdit_->text().trimmed(), {"devices"});
    if (!p.waitForStarted(3000)) {
        appendLog("error", tr("无法启动 adb，请检查路径（或 winget install scrcpy 自带 adb）"));
        return;
    }
    p.waitForFinished(5000);
    const QString out = QString::fromUtf8(p.readAllStandardOutput());
    for (const QString& line : out.split('\n', Qt::SkipEmptyParts)) {
        const QString t = line.trimmed();
        if (t.startsWith("List of devices") || t.startsWith("*")) continue;
        const QStringList parts = t.split(QRegularExpression("\\s+"), Qt::SkipEmptyParts);
        if (parts.size() >= 2 && parts[1] == "device") deviceCombo_->addItem(parts[0]);
    }
    appendLog("info", deviceCombo_->count()
                          ? tr("发现 %1 台设备").arg(deviceCombo_->count())
                          : tr("未发现设备，请确认已连接并授权 USB 调试"));
}

void CaptureTab::startCapture() {
    EngineSpec spec;
    QString err;
    if (!resolveEngine(spec, err)) {  // extract/btrecord 阶段需要引擎，先检查
        QMessageBox::warning(this, tr("提示"), err);
        return;
    }
    QString dir = outEdit_->text().trimmed();
    if (dir.isEmpty()) {
        dir = defaultOutDir();
        outEdit_->setText(dir);
    }
    if (!QDir().mkpath(dir)) {
        appendLog("error", tr("无法创建输出目录: %1").arg(dir));
        return;
    }
    outDir_ = dir;

    if (methodCombo_->currentIndex() == 1) {
        // ---- 蓝牙 A2DP：引擎 btrecord（打开 sink + 回环录制定长音频）----
        const QString wav = dir + "/cap.wav";
        capturing_ = true;
        startBtn_->setEnabled(false);
        stopBtn_->setEnabled(true);
        statusLabel_->setText(tr("A2DP 录制中… 请在手机蓝牙里连接本电脑并起播"));
        appendLog("info", tr("btrecord -> %1（%2 秒）。若提示无 A2DP sink 端点，"
                             "说明本机蓝牙驱动不支持，请改用 scrcpy 方式。")
                              .arg(wav).arg(btDurSpin_->value()));
        btProc_->start(spec.program,
                       spec.prefixArgs + QStringList{"btrecord", "-o", wav,
                                                     "--seconds",
                                                     QString::number(btDurSpin_->value())},
                       spec.workDir, toolEnv());
        return;
    }

    // ---- scrcpy ----
    QStringList args{"--no-video", "--no-control", "--audio-codec=flac",
                     QStringLiteral("--record=%1/cap.mkv").arg(dir)};
    const QString serial = deviceCombo_->currentText().trimmed();
    if (!serial.isEmpty()) args += {"-s", serial};
    capturing_ = true;
    startBtn_->setEnabled(false);
    stopBtn_->setEnabled(true);
    statusLabel_->setText(tr("录制中… 请在设备上起播，播完点“停止并抽取”"));
    appendLog("info", tr("scrcpy 录制 -> %1/cap.mkv").arg(dir));
    proc_->start(scrcpyEdit_->text().trimmed(), args, QString(), toolEnv());
}

void CaptureTab::stopCapture() {
    stopBtn_->setEnabled(false);
    if (methodCombo_->currentIndex() == 1) {
        // A2DP：定长录制，提前停止=终止（已录部分不保留，引擎为整段录制）
        btProc_->stop();
        capturing_ = false;
        startBtn_->setEnabled(true);
        statusLabel_->setText(tr("已提前终止 A2DP 录制（未保存）"));
        appendLog("warn", tr("A2DP 录制被提前终止，输出未保存（如需保留请等倒计时结束）"));
        return;
    }
    statusLabel_->setText(tr("停止录制，等待写出…"));
    proc_->stop();  // scrcpy 无优雅停止通道，kill 后 ffmpeg 通常仍可读出 flac 流
}

void CaptureTab::onRecordFinished(int exitCode) {
    if (!capturing_) return;
    capturing_ = false;
    startBtn_->setEnabled(true);
    appendLog("info", tr("录制进程结束 (退出码 %1)，开始抽取音频").arg(exitCode));

    EngineSpec spec;
    QString err;
    if (!resolveEngine(spec, err)) {
        appendLog("error", err);
        statusLabel_->setText(tr("引擎不可用"));
        return;
    }
    const QString wav = outDir_ + "/cap.wav";
    statusLabel_->setText(tr("抽取音频中…"));
    extractProc_->start(spec.program,
                        spec.prefixArgs + QStringList{"extract", outDir_ + "/cap.mkv",
                                                      "-o", wav},
                        spec.workDir, toolEnv());
}

void CaptureTab::onBtFinished(int exitCode) {
    if (!capturing_) return;  // 被提前终止
    capturing_ = false;
    startBtn_->setEnabled(true);
    stopBtn_->setEnabled(false);
    const QString wav = outDir_ + "/cap.wav";
    if (exitCode == 0 && QFileInfo::exists(wav)) {
        lastWav_ = wav;
        statusLabel_->setText(tr("完成: %1").arg(wav));
        appendLog("info", tr("A2DP 录制完成: %1").arg(wav));
        emit captured(wav);
    } else {
        statusLabel_->setText(tr("A2DP 录制失败 (退出码 %1)").arg(exitCode));
        appendLog("error", tr("btrecord 退出码 %1；若为 sink 端点缺失，请改用 scrcpy 方式").arg(exitCode));
    }
}

void CaptureTab::onExtractFinished(int exitCode) {
    const QString wav = outDir_ + "/cap.wav";
    if (exitCode == 0 && QFileInfo::exists(wav)) {
        lastWav_ = wav;
        statusLabel_->setText(tr("完成: %1").arg(wav));
        appendLog("info", tr("抽取完成: %1").arg(wav));
        emit captured(wav);
    } else {
        statusLabel_->setText(tr("抽取失败 (退出码 %1)").arg(exitCode));
        appendLog("error", tr("extract 退出码 %1；若缺 ffmpeg: winget install ffmpeg").arg(exitCode));
    }
}

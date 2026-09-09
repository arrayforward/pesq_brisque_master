#include "capture_tab.h"

#include "engine.h"
#include "proc_runner.h"

#include <cmath>

#include <QComboBox>
#include <QCoreApplication>
#include <QDateTime>
#include <QDir>
#include <QFile>
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
      btProc_(new ProcRunner(this)),
      sinkProc_(new ProcRunner(this)),
      listProc_(new ProcRunner(this)) {
    auto* layout = new QVBoxLayout(this);

    // ---- 采集方式 ----
    auto* methodRow = new QHBoxLayout;
    methodCombo_ = new QComboBox;
    methodCombo_->addItem(tr("scrcpy（USB 数字回采）"));
    methodCombo_->addItem(tr("蓝牙 A2DP（PC 模拟音响）"));
    methodCombo_->addItem(tr("麦克风 / Line-in（声学采集）"));
    btDurSpin_ = new QSpinBox;
    btDurSpin_->setRange(0, 86400);
    btDurSpin_->setValue(300);
    btDurSpin_->setSpecialValueText(tr("不限"));
    btDurSpin_->setSuffix(tr(" 秒"));
    methodRow->addWidget(new QLabel(tr("采集方式:")));
    methodRow->addWidget(methodCombo_, 1);
    methodRow->addWidget(new QLabel(tr("录制时长:")));
    methodRow->addWidget(btDurSpin_);
    layout->addLayout(methodRow);

    guideLabel_ = new QLabel;
    guideLabel_->setWordWrap(true);
    guideLabel_->setStyleSheet("color:#8ab4f8;");
    layout->addWidget(guideLabel_);

    // ---- 输入设备（仅 mic/Line-in 模式可见）----
    inDevWidget_ = new QWidget;
    auto* inDevRow = new QHBoxLayout(inDevWidget_);
    inDevRow->setContentsMargins(0, 0, 0, 0);
    inDevCombo_ = new QComboBox;
    inDevCombo_->setEditable(true);
    auto* inRefreshBtn = new QPushButton(tr("刷新"));
    inDevRow->addWidget(new QLabel(tr("输入设备:")));
    inDevRow->addWidget(inDevCombo_, 1);
    inDevRow->addWidget(inRefreshBtn);
    inDevWidget_->setVisible(false);
    layout->addWidget(inDevWidget_);
    connect(inRefreshBtn, &QPushButton::clicked, this, &CaptureTab::refreshInputDevices);

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
    connect(btProc_, &ProcRunner::finishedOk, this, &CaptureTab::onRecFinished);
    connect(btProc_, &ProcRunner::failedToStart, this, [this](const QString& m) {
        capturing_ = false;
        stopRequested_ = false;
        startBtn_->setEnabled(true);
        stopBtn_->setEnabled(false);
        statusLabel_->setText(tr("引擎启动失败"));
        appendLog("error", m);
    });
    connect(listProc_, &ProcRunner::line, this, &CaptureTab::onListLine);
    connect(listProc_, &ProcRunner::finishedOk, this, &CaptureTab::onListFinished);
    // btsink 进程：JSON 状态行驱动两步流程；进程意外退出复位到未打开
    connect(sinkProc_, &ProcRunner::line, this, &CaptureTab::onSinkLine);
    connect(sinkProc_, &ProcRunner::finishedOk, this, [this](int code) {
        if (methodCombo_->currentIndex() == 1 && a2dpStage_ != 3) {
            if (code != 0)
                appendLog("error", tr("btsink 退出 (退出码 %1)：若提示无 sink 端点，"
                                      "说明蓝牙驱动不支持，请改用 scrcpy 方式").arg(code));
            resetA2dpStage();
        }
    });
    connect(sinkProc_, &ProcRunner::failedToStart, this, [this](const QString& m) {
        appendLog("error", tr("btsink 启动失败: %1").arg(m));
        resetA2dpStage();
    });
    connect(methodCombo_, &QComboBox::currentIndexChanged, this, [this](int i) {
        // 模式切换时清理残留进程
        sinkProc_->stop();
        btProc_->stop();
        a2dpStage_ = 0;
        stopRequested_ = false;
        capturing_ = false;
        startBtn_->setEnabled(true);
        stopBtn_->setEnabled(false);
        deviceCombo_->setEnabled(i == 0);
        btDurSpin_->setEnabled(i != 0);
        inDevWidget_->setVisible(i == 2);
        if (i == 1) {
            startBtn_->setText(tr("打开蓝牙 Sink"));
            guideLabel_->setText(
                tr("A2DP 两步流程：① 点「打开蓝牙 Sink」（PC 对外呈现为蓝牙音响，等待连接）"
                   "② 手机「设置→蓝牙」连接本电脑，状态显示已连接后点「开始录制」\n"
                   "注意：A2DP 含蓝牙编解码损耗（通常 SBC），与 scrcpy 数字回采不是同一链路，分数不可跨通道比。"));
        } else if (i == 2) {
            startBtn_->setText(tr("开始采集"));
            guideLabel_->setText(
                tr("声学采集：手机外放对准 PC 麦克风（环境安静、距离角度固定保证可重复性）；"
                   "或有线直连：手机耳机口 → PC Line-in。\n"
                   "声学/有线链路与数字通道不是同一口径，分数不可跨通道比。"));
            refreshInputDevices();
        } else {
            startBtn_->setText(tr("开始采集（请先在设备上起播）"));
            guideLabel_->setText(
                tr("scrcpy 流程：adb 连接设备 → 设备上起播 →「开始采集」→ 播完点「停止并抽取」"));
        }
        statusLabel_->setText(tr("空闲"));
    });
    methodCombo_->setCurrentIndex(0);
    methodCombo_->currentIndexChanged(0);

    refreshDevices();
}

void CaptureTab::resetA2dpStage() {
    a2dpStage_ = 0;
    startBtn_->setEnabled(true);
    startBtn_->setText(tr("打开蓝牙 Sink"));
    statusLabel_->setText(tr("空闲"));
}

void CaptureTab::refreshInputDevices() {
    EngineSpec spec;
    QString err;
    if (!resolveEngine(spec, err)) {
        appendLog("error", err);
        return;
    }
    inDevCombo_->clear();
    inDevCombo_->addItem(tr("（枚举中…）"));
    listProc_->start(spec.program, spec.prefixArgs + QStringList{"micrecord", "--list"},
                     spec.workDir, toolEnv());
}

void CaptureTab::onListLine(const QString& line) {
    // micrecord --list 每行一个输入设备名
    if (line.isEmpty() || line.contains(QStringLiteral("警告"))
        || line.contains(QStringLiteral("错误")))
        return;
    if (inDevCombo_->count() && inDevCombo_->itemText(0).contains(QStringLiteral("枚举中")))
        inDevCombo_->clear();
    inDevCombo_->addItem(line);
}

void CaptureTab::onListFinished(int exitCode) {
    if (inDevCombo_->count() == 1
        && inDevCombo_->itemText(0).contains(QStringLiteral("枚举中")))
        inDevCombo_->clear();
    if (inDevCombo_->count() == 0)
        appendLog("warn", tr("输入设备枚举为空 (退出码 %1)：无可用麦克风/Line-in").arg(exitCode));
    else
        appendLog("info", tr("发现 %1 个输入设备").arg(inDevCombo_->count()));
}

void CaptureTab::onSinkLine(const QString& line) {
    if (!line.startsWith('{')) {  // 非 JSON 的普通日志行
        appendLog("info", line);
        return;
    }
    appendLog("info", line);
    if (line.contains(QStringLiteral("\"opened\""))) {
        if (a2dpStage_ == 1) {
            a2dpStage_ = 2;
            startBtn_->setEnabled(true);
            startBtn_->setText(tr("开始录制"));
            statusLabel_->setText(tr("手机已连接，可以开始录制"));
        }
    } else if (line.contains(QStringLiteral("\"closed\""))) {
        if (a2dpStage_ == 2) {
            a2dpStage_ = 1;
            startBtn_->setEnabled(false);
            statusLabel_->setText(tr("手机连接断开，等待重新连接…"));
        }
    } else if (line.contains(QStringLiteral("\"waiting\""))) {
        statusLabel_->setText(tr("等待手机连接…请在手机蓝牙设置里连接本电脑"));
    }
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
        // ---- 蓝牙 A2DP 两步：先 btsink 等手机连接，再 btrecord --no-sink 录制 ----
        if (a2dpStage_ <= 1) {
            // 阶段 1：打开 sink，等待手机连接
            if (!sinkProc_->running()) {
                a2dpStage_ = 1;
                startBtn_->setEnabled(false);
                stopBtn_->setEnabled(true);
                statusLabel_->setText(tr("等待手机连接…请在手机蓝牙设置里连接本电脑"));
                appendLog("info", tr("btsink 启动：PC 对外呈现为蓝牙音响，等待手机连接"));
                sinkProc_->start(spec.program,
                                 spec.prefixArgs + QStringList{"btsink"},
                                 spec.workDir, toolEnv());
            }
            return;
        }
        // 阶段 2：手机已连接，开始录制
        const QString wav = dir + "/cap.wav";
        capturing_ = true;
        a2dpStage_ = 3;
        startBtn_->setEnabled(false);
        stopBtn_->setEnabled(true);
        statusLabel_->setText(tr("A2DP 录制中…（btsink 保持连接）"));
        appendLog("info", tr("btrecord -> %1（%2 秒）").arg(wav).arg(btDurSpin_->value()));
        btProc_->start(spec.program,
                       spec.prefixArgs + QStringList{"btrecord", "-o", wav,
                                                     "--seconds",
                                                     QString::number(btDurSpin_->value()),
                                                     "--no-sink"},
                       spec.workDir, toolEnv());
        return;
    }

    if (methodCombo_->currentIndex() == 2) {
        // ---- mic/Line-in：引擎 micrecord（输入设备录制，分块写盘可提前停止）----
        const QString wav = dir + "/cap.wav";
        QStringList args = spec.prefixArgs + QStringList{"micrecord", "-o", wav,
                                                         "--seconds",
                                                         QString::number(btDurSpin_->value())};
        const QString dev = inDevCombo_->currentText().trimmed();
        if (!dev.isEmpty()) args += {"--device", dev};
        capturing_ = true;
        stopRequested_ = false;
        startBtn_->setEnabled(false);
        stopBtn_->setEnabled(true);
        statusLabel_->setText(tr("声学录制中…（提前停止会保留已录部分）"));
        appendLog("info", tr("micrecord -> %1（设备: %2）").arg(wav, dev.isEmpty() ? tr("默认") : dev));
        btProc_->start(spec.program, args, spec.workDir, toolEnv());
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
    const int mode = methodCombo_->currentIndex();
    if (mode == 1 || mode == 2) {
        // A2DP/mic 提前停止 = 正常结束本次录制：引擎分块写盘+逐块 flush，
        // 强杀后已录部分仍在盘上且 header 有效（见 btrecord.py 注释）。
        // 终止录制进程，等 onRecFinished 对已录部分做静音校验后照常送评估。
        stopRequested_ = true;
        btProc_->stop();
        statusLabel_->setText(tr("正在停止，保留已录部分…"));
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

// 读取 PCM16 wav 的 RMS（跳过前 1024 字节 header 区域，仅用于静音校验）
static double wavRms16(const QString& path) {
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly)) return 0.0;
    f.skip(1024);
    const QByteArray data = f.readAll();
    const int n = data.size() / 2;
    if (n <= 0) return 0.0;
    const qint16* s = reinterpret_cast<const qint16*>(data.constData());
    double sum = 0.0;
    for (int i = 0; i < n; ++i) {
        const double v = s[i] / 32768.0;
        sum += v * v;
    }
    return std::sqrt(sum / n);
}

void CaptureTab::onRecFinished(int exitCode) {
    const int mode = methodCombo_->currentIndex();
    if (mode != 1 && mode != 2) return;
    const bool early = stopRequested_;
    stopRequested_ = false;
    if (!capturing_) return;  // 已被模式切换清理
    capturing_ = false;
    if (mode == 1) {
        sinkProc_->stop();  // A2DP：录制结束，关闭 sink
        a2dpStage_ = 0;
        startBtn_->setText(tr("打开蓝牙 Sink"));
    }
    startBtn_->setEnabled(true);
    stopBtn_->setEnabled(false);
    const QString wav = outDir_ + "/cap.wav";
    QFileInfo fi(wav);
    // 提前停止（被强杀）时退出码非 0 属正常——以文件是否存在且非空为准
    if (!fi.exists() || fi.size() < 2048) {
        statusLabel_->setText(tr("A2DP 录制失败，无有效输出 (退出码 %1)").arg(exitCode));
        appendLog("error", tr("btrecord 退出码 %1，无有效输出文件").arg(exitCode));
        return;
    }
    // 对已录部分做静音校验（引擎正常结束时会自校，提前被杀时由这里兜底）
    const double rms = wavRms16(wav);
    const double secs = (fi.size() - 44) / 96000.0;  // 48kHz/16bit 单声道
    if (rms < 1e-5) {
        statusLabel_->setText(tr("录制结果接近全静音！手机未连接/未播放"));
        appendLog("warn", tr("录制结果接近全静音（RMS=%1），请检查手机连接与播放").arg(rms));
        return;
    }
    lastWav_ = wav;
    if (early) {
        statusLabel_->setText(tr("已停止，保留已录 %1 秒: %2").arg(secs, 0, 'f', 0).arg(wav));
        appendLog("info", tr("提前停止，保留已录 %1 秒 → %2").arg(secs, 0, 'f', 1).arg(wav));
    } else {
        statusLabel_->setText(tr("完成: %1").arg(wav));
        appendLog("info", tr("A2DP 录制完成: %1").arg(wav));
    }
    emit captured(wav);
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

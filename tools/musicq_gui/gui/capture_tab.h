#pragma once

#include <QWidget>

class QLineEdit;
class QComboBox;
class QPushButton;
class QLabel;
class QPlainTextEdit;
class QSpinBox;
class QProcessEnvironment;
class ProcRunner;

// 采集 Tab: scrcpy 录制设备播放输出 -> 自动调引擎 extract 抽取 48k 单声道 wav
class CaptureTab : public QWidget {
    Q_OBJECT
public:
    explicit CaptureTab(QWidget* parent = nullptr);

signals:
    // 抽取完成，wavPath 供评估 Tab 使用
    void captured(const QString& wavPath);

private slots:
    void refreshDevices();
    void browseOut();
    void startCapture();
    void stopCapture();
    void onRecordFinished(int exitCode);
    void onExtractFinished(int exitCode);
    void onBtFinished(int exitCode);
    void onSinkLine(const QString& line);
    void resetA2dpStage();

private:
    void appendLog(const QString& level, const QString& msg);
    QString defaultOutDir() const;
    QProcessEnvironment toolEnv() const;  // PATH 前置 scrcpy/ffmpeg 所在目录

    QComboBox* methodCombo_;   // 采集方式: 0=scrcpy(USB) 1=蓝牙 A2DP
    QSpinBox* btDurSpin_;      // A2DP 录制时长(秒)
    QLabel* guideLabel_;       // 随方式切换的操作指引
    QLineEdit* scrcpyEdit_;
    QLineEdit* ffmpegEdit_;
    QLineEdit* adbEdit_;
    QComboBox* deviceCombo_;
    QLineEdit* outEdit_;
    QPushButton* startBtn_;
    QPushButton* stopBtn_;
    QLabel* statusLabel_;
    QPlainTextEdit* log_;

    ProcRunner* proc_;       // scrcpy 录制进程
    ProcRunner* extractProc_;// 引擎 extract 进程
    ProcRunner* btProc_;     // 引擎 btrecord 进程（A2DP 录制）
    ProcRunner* sinkProc_;   // 引擎 btsink 进程（A2DP sink 保持）
    int a2dpStage_ = 0;      // A2DP 阶段: 0=未打开 1=等待连接 2=已连接 3=录制中
    bool stopRequested_ = false; // A2DP 录制中被用户提前停止（保留已录部分）
    QString outDir_;
    QString lastWav_;
    bool capturing_ = false;
};

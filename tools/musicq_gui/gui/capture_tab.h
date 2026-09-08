#pragma once

#include <QWidget>

class QLineEdit;
class QComboBox;
class QPushButton;
class QLabel;
class QPlainTextEdit;
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

private:
    void appendLog(const QString& level, const QString& msg);
    QString defaultOutDir() const;
    QProcessEnvironment toolEnv() const;  // PATH 前置 scrcpy/ffmpeg 所在目录

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
    QString outDir_;
    QString lastWav_;
    bool capturing_ = false;
};

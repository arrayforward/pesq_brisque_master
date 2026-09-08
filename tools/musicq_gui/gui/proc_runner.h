#pragma once

#include <QObject>
#include <QProcess>
#include <QString>
#include <QStringList>

// QProcess 简单封装：行式输出回调 + 结束信号，子进程默认 UTF-8 输出环境
class ProcRunner : public QObject {
    Q_OBJECT
public:
    explicit ProcRunner(QObject* parent = nullptr);
    ~ProcRunner() override;

    // extraEnv 在系统环境基础上叠加（如 PATH 前置 ffmpeg 目录）
    void start(const QString& program, const QStringList& args,
               const QString& workDir = QString(),
               const QProcessEnvironment& extraEnv = QProcessEnvironment());
    void stop();  // 强杀（scrcpy 等无优雅退出通道的场景）
    bool running() const;

signals:
    void line(const QString& text);      // stdout/stderr 合流后的行
    void finishedOk(int exitCode);
    void failedToStart(const QString& msg);

private slots:
    void onReadyRead();

private:
    QProcess* proc_;
    QByteArray buf_;  // 行缓冲（输出可能不按行刷新）
};

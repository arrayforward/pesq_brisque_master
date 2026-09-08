#include "proc_runner.h"

ProcRunner::ProcRunner(QObject* parent) : QObject(parent), proc_(new QProcess(this)) {
    proc_->setProcessChannelMode(QProcess::MergedChannels);
    connect(proc_, &QProcess::readyReadStandardOutput, this, &ProcRunner::onReadyRead);
    connect(proc_, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
            this, [this](int code, QProcess::ExitStatus) {
                onReadyRead();
                if (!buf_.isEmpty()) {  // 冲刷残余半行
                    emit line(QString::fromUtf8(buf_));
                    buf_.clear();
                }
                emit finishedOk(code);
            });
    connect(proc_, &QProcess::errorOccurred, this, [this](QProcess::ProcessError e) {
        if (e == QProcess::FailedToStart)
            emit failedToStart(proc_->errorString());
    });
}

ProcRunner::~ProcRunner() {
    if (proc_->state() != QProcess::NotRunning) {
        proc_->kill();
        proc_->waitForFinished(3000);
    }
}

void ProcRunner::start(const QString& program, const QStringList& args,
                       const QString& workDir, const QProcessEnvironment& extraEnv) {
    QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
    env.insert("PYTHONIOENCODING", "utf-8");  // 引擎中文日志按 UTF-8 输出
    const QStringList keys = extraEnv.keys();
    for (const QString& k : keys) env.insert(k, extraEnv.value(k));
    proc_->setProcessEnvironment(env);
    if (!workDir.isEmpty()) proc_->setWorkingDirectory(workDir);
    proc_->start(program, args);
}

void ProcRunner::stop() {
    if (proc_->state() != QProcess::NotRunning) proc_->kill();
}

bool ProcRunner::running() const { return proc_->state() != QProcess::NotRunning; }

void ProcRunner::onReadyRead() {
    buf_ += proc_->readAllStandardOutput();
    int idx;
    while ((idx = buf_.indexOf('\n')) >= 0) {
        emit line(QString::fromUtf8(buf_.left(idx)).trimmed());
        buf_.remove(0, idx + 1);
    }
}

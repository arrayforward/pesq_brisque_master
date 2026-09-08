#include "gen_tab.h"

#include "engine.h"
#include "proc_runner.h"

#include <QDateTime>
#include <QFileDialog>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QLabel>
#include <QLineEdit>
#include <QMessageBox>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QVBoxLayout>

GenTab::GenTab(QWidget* parent) : QWidget(parent), proc_(new ProcRunner(this)) {
    auto* layout = new QVBoxLayout(this);

    auto* group = new QGroupBox(tr("测试音频生成（叠加 leader + chirp 导频）"));
    auto* form = new QVBoxLayout(group);

    auto* srcRow = new QHBoxLayout;
    srcEdit_ = new QLineEdit(QStringLiteral("D:/music"));
    auto* srcBtn = new QPushButton(tr("浏览…"));
    srcRow->addWidget(new QLabel(tr("曲库目录/文件:")));
    srcRow->addWidget(srcEdit_, 1);
    srcRow->addWidget(srcBtn);
    form->addLayout(srcRow);

    auto* outRow = new QHBoxLayout;
    outEdit_ = new QLineEdit(QStringLiteral("D:/music/musicq_out"));
    auto* outBtn = new QPushButton(tr("浏览…"));
    outRow->addWidget(new QLabel(tr("输出目录:")));
    outRow->addWidget(outEdit_, 1);
    outRow->addWidget(outBtn);
    form->addLayout(outRow);
    layout->addWidget(group);

    startBtn_ = new QPushButton(tr("开始生成"));
    layout->addWidget(startBtn_);

    statusLabel_ = new QLabel(tr("空闲"));
    layout->addWidget(statusLabel_);

    log_ = new QPlainTextEdit;
    log_->setReadOnly(true);
    log_->setMaximumBlockCount(5000);
    layout->addWidget(log_, 1);

    connect(srcBtn, &QPushButton::clicked, this, &GenTab::browseSrc);
    connect(outBtn, &QPushButton::clicked, this, &GenTab::browseOut);
    connect(startBtn_, &QPushButton::clicked, this, &GenTab::startGen);
    connect(proc_, &ProcRunner::line, this, [this](const QString& l) { appendLog("info", l); });
    connect(proc_, &ProcRunner::finishedOk, this, [this](int code) {
        startBtn_->setEnabled(true);
        statusLabel_->setText(code == 0 ? tr("生成完成") : tr("生成失败 (退出码 %1)").arg(code));
        appendLog(code == 0 ? "info" : "error",
                  code == 0 ? tr("gen 完成") : tr("gen 退出码 %1").arg(code));
    });
    connect(proc_, &ProcRunner::failedToStart, this, [this](const QString& m) {
        startBtn_->setEnabled(true);
        appendLog("error", tr("引擎启动失败: %1").arg(m));
    });
}

void GenTab::appendLog(const QString& level, const QString& msg) {
    const QString ts = QDateTime::currentDateTime().toString("hh:mm:ss");
    QString color = "#ccc";
    if (level == "warn") color = "#e6b800";
    else if (level == "error") color = "#ff5555";
    log_->appendHtml(QString("<span style='color:#888'>[%1]</span> <span style='color:%2'>%3</span>")
                         .arg(ts, color, msg.toHtmlEscaped()));
}

void GenTab::browseSrc() {
    const QString d = QFileDialog::getExistingDirectory(this, tr("选择曲库目录"),
                                                        srcEdit_->text());
    if (!d.isEmpty()) srcEdit_->setText(d);
}

void GenTab::browseOut() {
    const QString d = QFileDialog::getExistingDirectory(this, tr("选择输出目录"),
                                                        outEdit_->text());
    if (!d.isEmpty()) outEdit_->setText(d);
}

void GenTab::startGen() {
    EngineSpec spec;
    QString err;
    if (!resolveEngine(spec, err)) {
        QMessageBox::warning(this, tr("提示"), err);
        return;
    }
    const QString src = srcEdit_->text().trimmed();
    const QString out = outEdit_->text().trimmed();
    if (src.isEmpty() || out.isEmpty()) {
        QMessageBox::warning(this, tr("提示"), tr("请填写曲库目录与输出目录"));
        return;
    }
    startBtn_->setEnabled(false);
    statusLabel_->setText(tr("生成中…"));
    appendLog("info", tr("引擎: %1").arg(spec.describe));
    appendLog("info", tr("gen %1 -> %2").arg(src, out));
    proc_->start(spec.program,
                 spec.prefixArgs + QStringList{"gen", src, "-o", out},
                 spec.workDir);
}

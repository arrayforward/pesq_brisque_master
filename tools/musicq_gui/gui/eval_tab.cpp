#include "eval_tab.h"

#include "chart_widget.h"
#include "engine.h"
#include "proc_runner.h"

#include <algorithm>

#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileDialog>
#include <QFileInfo>
#include <QHBoxLayout>
#include <QHeaderView>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QLineEdit>
#include <QMessageBox>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QSplitter>
#include <QTableWidget>
#include <QTextEdit>
#include <QVBoxLayout>
#include <QtNumeric>

EvalTab::EvalTab(QWidget* parent) : QWidget(parent), proc_(new ProcRunner(this)) {
    auto* layout = new QVBoxLayout(this);

    // ---- 输入 ----
    auto mkRow = [&](const QString& label, QLineEdit*& edit, const QString& def,
                     bool file, const QString& hint) {
        auto* row = new QHBoxLayout;
        edit = new QLineEdit(def);
        auto* btn = new QPushButton(tr("浏览…"));
        row->addWidget(new QLabel(label));
        row->addWidget(edit, 1);
        row->addWidget(btn);
        layout->addLayout(row);
        if (file)
            connect(btn, &QPushButton::clicked, this, [this, edit, hint] {
                const QString f = QFileDialog::getOpenFileName(this, hint, edit->text(),
                                                               "WAV 音频 (*.wav)");
                if (!f.isEmpty()) edit->setText(f);
            });
        else
            connect(btn, &QPushButton::clicked, this, [this, edit, hint] {
                const QString d = QFileDialog::getExistingDirectory(this, hint, edit->text());
                if (!d.isEmpty()) edit->setText(d);
            });
    };
    mkRow(tr("采集 wav:"), wavEdit_, {}, true, tr("选择采集 wav"));
    mkRow(tr("参考目录:"), refEdit_, QStringLiteral("D:/music/musicq_out"), false,
          tr("含 *_test.wav / *_markers.json 的目录"));
    mkRow(tr("报告输出:"), outEdit_, {}, false, tr("报告输出目录"));

    startBtn_ = new QPushButton(tr("开始评估（自动识别歌曲与循环）"));
    layout->addWidget(startBtn_);

    statusLabel_ = new QLabel(tr("空闲"));
    layout->addWidget(statusLabel_);

    // ---- 结果区：实例表 + 聚合 + 曲线 + 日志 ----
    auto* splitter = new QSplitter(Qt::Vertical);
    auto* topRow = new QSplitter(Qt::Horizontal);

    instTable_ = new QTableWidget(0, 7);
    instTable_->setHorizontalHeaderLabels({tr("实例"), tr("歌名"), tr("循环"),
                                           tr("开始(s)"), tr("结束(s)"),
                                           tr("ViSQOL中位"), tr("SNR中位(dB)")});
    instTable_->horizontalHeader()->setStretchLastSection(true);
    topRow->addWidget(instTable_);

    summaryView_ = new QTextEdit;
    summaryView_->setReadOnly(true);
    topRow->addWidget(summaryView_);
    topRow->setStretchFactor(0, 2);
    topRow->setStretchFactor(1, 1);
    splitter->addWidget(topRow);

    chart_ = new ChartWidget;
    splitter->addWidget(chart_);

    log_ = new QPlainTextEdit;
    log_->setReadOnly(true);
    log_->setMaximumBlockCount(8000);
    splitter->addWidget(log_);
    splitter->setStretchFactor(1, 1);
    layout->addWidget(splitter, 1);

    connect(startBtn_, &QPushButton::clicked, this, &EvalTab::startEval);
    connect(proc_, &ProcRunner::line, this, [this](const QString& l) { appendLog("info", l); });
    connect(proc_, &ProcRunner::finishedOk, this, [this](int code) {
        startBtn_->setEnabled(true);
        if (code == 0) {
            statusLabel_->setText(tr("评估完成"));
            showResults();
        } else {
            statusLabel_->setText(tr("评估失败 (退出码 %1)").arg(code));
        }
    });
    connect(proc_, &ProcRunner::failedToStart, this, [this](const QString& m) {
        startBtn_->setEnabled(true);
        appendLog("error", tr("引擎启动失败: %1").arg(m));
    });
}

void EvalTab::setWav(const QString& wavPath) {
    wavEdit_->setText(wavPath);
    if (outEdit_->text().isEmpty())
        outEdit_->setText(QFileInfo(wavPath).absolutePath() + "/report");
}

void EvalTab::appendLog(const QString& level, const QString& msg) {
    const QString ts = QDateTime::currentDateTime().toString("hh:mm:ss");
    QString color = "#ccc";
    if (level == "warn") color = "#e6b800";
    else if (level == "error") color = "#ff5555";
    log_->appendHtml(QString("<span style='color:#888'>[%1]</span> <span style='color:%2'>%3</span>")
                         .arg(ts, color, msg.toHtmlEscaped()));
}

void EvalTab::startEval() {
    EngineSpec spec;
    QString err;
    if (!resolveEngine(spec, err)) {
        QMessageBox::warning(this, tr("提示"), err);
        return;
    }
    const QString wav = wavEdit_->text().trimmed();
    const QString ref = refEdit_->text().trimmed();
    QString out = outEdit_->text().trimmed();
    if (wav.isEmpty() || ref.isEmpty()) {
        QMessageBox::warning(this, tr("提示"), tr("请选择采集 wav 与参考目录"));
        return;
    }
    if (out.isEmpty()) {
        out = QFileInfo(wav).absolutePath() + "/report";
        outEdit_->setText(out);
    }
    startBtn_->setEnabled(false);
    statusLabel_->setText(tr("评估中（ViSQOL 逐段评分，耗时与时长成正比）…"));
    appendLog("info", tr("autoscore %1 --ref-dir %2 -o %3").arg(wav, ref, out));
    proc_->start(spec.program,
                 spec.prefixArgs + QStringList{"autoscore", wav, "--ref-dir", ref,
                                               "-o", out},
                 spec.workDir);
}

// 简单 CSV 行解析（处理引号包裹）
static QStringList parseCsvLine(const QString& line) {
    QStringList out;
    QString cur;
    bool inQ = false;
    for (const QChar& ch : line) {
        if (inQ) {
            if (ch == '"') inQ = false;
            else cur += ch;
        } else if (ch == '"') {
            inQ = true;
        } else if (ch == ',') {
            out << cur;
            cur.clear();
        } else {
            cur += ch;
        }
    }
    out << cur;
    return out;
}

static double medianOf(QVector<double> v) {
    if (v.isEmpty()) return qQNaN();
    std::sort(v.begin(), v.end());
    const int n = v.size();
    return n % 2 ? v[n / 2] : (v[n / 2 - 1] + v[n / 2]) / 2.0;
}

void EvalTab::showResults() {
    const QString out = outEdit_->text().trimmed();

    // ---- autoscore.json: 实例信息 ----
    QMap<int, QJsonObject> instByIdx;
    QFile aj(out + "/autoscore.json");
    if (aj.open(QIODevice::ReadOnly)) {
        const auto doc = QJsonDocument::fromJson(aj.readAll());
        for (const auto& v : doc.object()["instances"].toArray())
            instByIdx[v.toObject()["instance"].toInt()] = v.toObject();
    }

    // ---- report.csv: 逐段分，按实例聚合 ----
    struct Acc { QString song; int loop = 0; QVector<double> vis, snr; };
    QMap<int, Acc> acc;
    QFile cf(out + "/report.csv");
    if (cf.open(QIODevice::ReadOnly | QIODevice::Text)) {
        const QString text = QString::fromUtf8(cf.readAll());
        const QStringList lines = text.split('\n', Qt::SkipEmptyParts);
        if (!lines.isEmpty()) {
            const QStringList hdr = parseCsvLine(lines[0]);
            const int iInst = hdr.indexOf("instance"), iSong = hdr.indexOf("song_id"),
                      iVis = hdr.indexOf("visqol"), iSnr = hdr.indexOf("snr_db");
            for (int li = 1; li < lines.size(); ++li) {
                const QStringList f = parseCsvLine(lines[li]);
                if (f.size() <= qMax(qMax(iInst, iVis), qMax(iSong, iSnr))) continue;
                auto& a = acc[f[iInst].toInt()];
                a.song = f[iSong];
                if (iVis >= 0 && !f[iVis].isEmpty()) a.vis << f[iVis].toDouble();
                if (iSnr >= 0 && !f[iSnr].isEmpty()) a.snr << f[iSnr].toDouble();
            }
        }
    }

    instTable_->setRowCount(0);
    for (auto it = instByIdx.begin(); it != instByIdx.end(); ++it) {
        const QJsonObject o = it.value();
        const int row = instTable_->rowCount();
        instTable_->insertRow(row);
        const Acc& a = acc[it.key()];
        const QString song = o["song_id"].toString(a.song);
        auto set = [&](int col, const QString& s) {
            instTable_->setItem(row, col, new QTableWidgetItem(s));
        };
        set(0, QString::number(it.key()));
        set(1, song);
        set(2, QString::number(o["loop"].toInt()));
        set(3, QString::number(o["rec_start_s"].toDouble(), 'f', 1));
        set(4, QString::number(o["rec_end_s"].toDouble(), 'f', 1));
        set(5, a.vis.isEmpty() ? "-" : QString::number(medianOf(a.vis), 'f', 3));
        set(6, a.snr.isEmpty() ? "-" : QString::number(medianOf(a.snr), 'f', 1));
    }

    // ---- summary.txt 原文（含按歌曲聚合 + 总体聚合）----
    QFile sf_(out + "/summary.txt");
    if (sf_.open(QIODevice::ReadOnly | QIODevice::Text))
        summaryView_->setPlainText(QString::fromUtf8(sf_.readAll()));

    chart_->loadImage(out + "/quality.png");
}

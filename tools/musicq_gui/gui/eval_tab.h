#pragma once

#include <QWidget>

class QLineEdit;
class QPushButton;
class QLabel;
class QPlainTextEdit;
class QTableWidget;
class QTextEdit;
class ChartWidget;
class ProcRunner;

// 评估 Tab: 选采集 wav + 参考目录 -> 调引擎 autoscore -> 实例表 + 聚合 + 质量曲线
class EvalTab : public QWidget {
    Q_OBJECT
public:
    explicit EvalTab(QWidget* parent = nullptr);

    void setWav(const QString& wavPath);  // 供采集 Tab 完成后联动填入

private slots:
    void startEval();

private:
    void appendLog(const QString& level, const QString& msg);
    void showResults();  // 解析 autoscore.json + report.csv + summary.txt + quality.png

    QLineEdit* wavEdit_;
    QLineEdit* refEdit_;
    QLineEdit* outEdit_;
    QPushButton* startBtn_;
    QLabel* statusLabel_;
    QTableWidget* instTable_;
    QTextEdit* summaryView_;
    ChartWidget* chart_;
    QPlainTextEdit* log_;
    ProcRunner* proc_;
};

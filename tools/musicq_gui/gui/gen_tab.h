#pragma once

#include <QWidget>

class QLineEdit;
class QPushButton;
class QLabel;
class QPlainTextEdit;
class ProcRunner;

// 音源生成 Tab: 选曲库目录 -> 调引擎 gen 批量生成带 leader/chirp 的测试音频
class GenTab : public QWidget {
    Q_OBJECT
public:
    explicit GenTab(QWidget* parent = nullptr);

private slots:
    void browseSrc();
    void browseOut();
    void startGen();

private:
    void appendLog(const QString& level, const QString& msg);

    QLineEdit* srcEdit_;
    QLineEdit* outEdit_;
    QPushButton* startBtn_;
    QLabel* statusLabel_;
    QPlainTextEdit* log_;
    ProcRunner* proc_;
};

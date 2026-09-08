#pragma once

#include <QMainWindow>

class QTabWidget;
class EvalTab;
class CaptureTab;

// 主窗口：音源生成 / 采集 / 评估 三个 Tab
class MainWindow : public QMainWindow {
    Q_OBJECT
public:
    explicit MainWindow(QWidget* parent = nullptr);

private:
    QTabWidget* tabs_;
    EvalTab* evalTab_;
    CaptureTab* captureTab_;
};

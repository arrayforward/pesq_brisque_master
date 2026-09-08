#pragma once

#include <QLabel>
#include <QPixmap>

// 质量曲线显示：加载引擎生成的 quality.png，随窗口缩放等比适配
class ChartWidget : public QLabel {
    Q_OBJECT
public:
    explicit ChartWidget(QWidget* parent = nullptr);
    void loadImage(const QString& path);

protected:
    void resizeEvent(QResizeEvent* e) override;

private:
    QPixmap pix_;
};

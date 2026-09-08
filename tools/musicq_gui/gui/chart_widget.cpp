#include "chart_widget.h"

ChartWidget::ChartWidget(QWidget* parent) : QLabel(parent) {
    setAlignment(Qt::AlignCenter);
    setMinimumSize(400, 240);
    setStyleSheet("background:#222; color:#888;");
    setText(tr("尚无质量曲线"));
}

void ChartWidget::loadImage(const QString& path) {
    QPixmap p(path);
    if (p.isNull()) {
        setText(tr("质量曲线加载失败: %1").arg(path));
        pix_ = QPixmap();
        return;
    }
    pix_ = p;
    setPixmap(pix_.scaled(size(), Qt::KeepAspectRatio, Qt::SmoothTransformation));
}

void ChartWidget::resizeEvent(QResizeEvent* e) {
    QLabel::resizeEvent(e);
    if (!pix_.isNull())
        setPixmap(pix_.scaled(size(), Qt::KeepAspectRatio, Qt::SmoothTransformation));
}

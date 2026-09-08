#include <QApplication>

#include "main_window.h"

int main(int argc, char* argv[]) {
    QApplication app(argc, argv);
    QApplication::setApplicationName(QStringLiteral("musicq_tool"));
    QApplication::setOrganizationName(QStringLiteral("musicq"));
    MainWindow w;
    w.show();
    return QApplication::exec();
}

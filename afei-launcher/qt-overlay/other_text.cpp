// SPDX-License-Identifier: GPL-3.0-or-later
#include "other_text.h"

#include <QAbstractItemModel>
#include <QComboBox>
#include <QEvent>
#include <QMessageBox>
#include <QRegularExpression>
#include <QStringList>
#include <QStyledItemDelegate>
#include <QTreeView>

namespace {

class AddOnTextDelegate final : public QStyledItemDelegate {
public:
    using QStyledItemDelegate::QStyledItemDelegate;

    QString displayText(const QVariant& value, const QLocale& locale) const override {
        const QString text = QStyledItemDelegate::displayText(value, locale);
        if (!text.contains(QLatin1StringView("Update"))) {
            return text;
        }
        static const QRegularExpression update(
            QStringLiteral(R"(^(\[D\] )?Update( \((?:NAND|SDMC)\))?( \((?:[0-9]+(?:\.[0-9]+)*|NCA|NRO|NSP|NSZ|NUP|NX|XCI|XCZ)\))?$)"));
        QStringList lines = text.split(QChar::LineSeparator);
        for (QString& line : lines) {
            const auto match = update.match(line);
            if (match.hasMatch()) {
                const QString disabled = match.hasCaptured(1) ? QStringLiteral("[已停用] ") : QString();
                line = disabled + QStringLiteral("更新") + match.captured(2) + match.captured(3);
            }
        }
        return lines.join(QChar::LineSeparator);
    }
};

void translateThemes(QComboBox* combo) {
    if (combo->objectName() != QLatin1StringView("theme_combobox")) {
        return;
    }
    QWidget* panel = combo->parentWidget();
    while (panel && !panel->inherits("ConfigureUi")) {
        panel = panel->parentWidget();
    }
    if (!panel) {
        return;
    }
    struct Theme {
        const char* id;
        const char* english;
        const char16_t* chinese;
    };
    static constexpr Theme themes[] = {
        {"default", "Default", u"默认"},
        {"colorful", "Default Colorful", u"默认彩色"},
        {"qdarkstyle", "Dark", u"深色"},
        {"colorful_dark", "Dark Colorful", u"深色彩色"},
        {"qdarkstyle_midnight_blue", "Midnight Blue", u"午夜蓝"},
        {"colorful_midnight_blue", "Midnight Blue Colorful", u"午夜蓝彩色"},
    };
    for (const Theme& theme : themes) {
        const int index = combo->findData(QString::fromLatin1(theme.id));
        if (index >= 0 && combo->itemText(index) == QLatin1StringView(theme.english)) {
            combo->setItemText(index, QString::fromUtf16(theme.chinese));
        }
    }
}

} // namespace

void translateOtherText(QObject* receiver, QEvent* event) {
    if (event->type() != QEvent::Show) {
        return;
    }
    if (auto* combo = qobject_cast<QComboBox*>(receiver)) {
        translateThemes(combo);
        return;
    }
    if (auto* message = qobject_cast<QMessageBox*>(receiver)) {
        if (message->windowTitle() == QLatin1StringView("Error Removing <Invalid Type>")) {
            message->setWindowTitle(QStringLiteral("移除失败（类型无效）"));
        }
        if (message->text() == QLatin1StringView("Remove Installed Game <Invalid Type>?")) {
            message->setText(QStringLiteral("移除已安装的游戏（类型无效）？"));
        }
        return;
    }
    auto* view = qobject_cast<QTreeView*>(receiver);
    constexpr int addOnsColumn = 4;
    if (view && view->inherits("GameTree") && view->model() && view->model()->inherits("GameListModel")
        && !view->itemDelegateForColumn(addOnsColumn) && view->itemDelegate()
        && view->itemDelegate()->metaObject() == &QStyledItemDelegate::staticMetaObject) {
        // GameListItemPath parses the original version text for its tooltip.
        view->setItemDelegateForColumn(addOnsColumn, new AddOnTextDelegate(view));
    }
}

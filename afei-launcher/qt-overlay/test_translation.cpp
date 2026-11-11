// SPDX-License-Identifier: GPL-3.0-or-later

#include <QAction>
#include <QApplication>
#include <QComboBox>
#include <QContextMenuEvent>
#include <QDebug>
#include <QDialog>
#include <QLabel>
#include <QMainWindow>
#include <QMenu>
#include <QPushButton>
#include <QStatusBar>
#include <QStandardItemModel>
#include <QStyledItemDelegate>
#include <QTreeView>
#include <QVBoxLayout>

class MainWindow final : public QMainWindow {
    Q_OBJECT
};

class QtSoftwareKeyboardDialog final : public QDialog {
    Q_OBJECT
};

class ConfigureUi final : public QWidget {
    Q_OBJECT
};

class GameTree final : public QTreeView {
    Q_OBJECT
};

class GameListModel final : public QStandardItemModel {
    Q_OBJECT
};

bool check(QStringView actual, QStringView expected) {
    if (actual == expected) {
        return true;
    }
    qCritical() << "Expected" << expected << "but got" << actual;
    return false;
}

bool checkSettingsAndAddOns() {
    ConfigureUi settings;
    QComboBox themes(&settings);
    themes.setObjectName(QStringLiteral("theme_combobox"));
    themes.addItem(QStringLiteral("Dark"), QStringLiteral("qdarkstyle"));
    settings.show();
    if (!check(themes.currentText(), u"深色") || !check(themes.currentData().toString(), u"qdarkstyle")) {
        return false;
    }

    const QString english = QStringLiteral("Update (1.2.3)\n[D] Update (SDMC) (2.0)\nAnother Update (4.0)");
    GameListModel model;
    model.setItem(0, 4, new QStandardItem(english));
    GameTree tree;
    tree.setModel(&model);
    tree.show();
    auto* delegate = qobject_cast<QStyledItemDelegate*>(tree.itemDelegateForColumn(4));
    return delegate && check(delegate->displayText(model.index(0, 4).data(), QLocale()),
                             u"更新 (1.2.3)\u2028[已停用] 更新 (SDMC) (2.0)\u2028Another Update (4.0)")
           && check(model.index(0, 4).data().toString(), english);
}

int main(int argc, char* argv[]) {
    // Both emulator versions construct a temporary QApplication before the real one.
    {
        int temporary_argc = 0;
        QApplication temporary_app(temporary_argc, nullptr);
    }
    QApplication app(argc, argv);

    MainWindow main;
    QPushButton dock(QStringLiteral("DOCKED"));
    dock.setObjectName(QStringLiteral("DockingStatusBarButton"));
    dock.setCheckable(true);
    dock.setChecked(true);
    dock.setContextMenuPolicy(Qt::CustomContextMenu);
    main.statusBar()->addPermanentWidget(&dock);
    main.show();
    if (!check(dock.text(), u"主机模式") || !dock.isChecked()) {
        return 1;
    }

    bool menu_translated = false;
    QObject::connect(&dock, &QPushButton::customContextMenuRequested, &dock, [&] {
        QMenu menu;
        const auto* handheld_action = menu.addAction(QStringLiteral("Handheld"));
        const auto* docked_action = menu.addAction(QStringLiteral("Docked"));
        menu.show();
        menu_translated = check(handheld_action->text(), u"掌机模式")
                          && check(docked_action->text(), u"主机模式");
    });
    QContextMenuEvent context_menu(QContextMenuEvent::Mouse, QPoint{}, QPoint{});
    QApplication::sendEvent(&dock, &context_menu);
    if (!menu_translated) {
        return 1;
    }
    QMenu unrelated_menu;
    const auto* unrelated_action = unrelated_menu.addAction(QStringLiteral("Docked"));
    unrelated_menu.addAction(QStringLiteral("Handheld"));
    unrelated_menu.show();
    if (!check(unrelated_action->text(), u"Docked")) {
        return 1;
    }
    unrelated_menu.hide();

    dock.setChecked(false);
    dock.setText(QStringLiteral("HANDHELD"));
    app.processEvents();
    if (!check(dock.text(), u"掌机模式") || dock.isChecked()) {
        return 1;
    }

    QtSoftwareKeyboardDialog keyboard;
    QVBoxLayout layout(&keyboard);
    QLabel caps(QStringLiteral("Caps Lock"));
    caps.setObjectName(QStringLiteral("label_shift_shift"));
    layout.addWidget(&caps);
    QPushButton space(QStringLiteral("Space"));
    space.setObjectName(QStringLiteral("button_space"));
    layout.addWidget(&space);
    QLabel game_text(QStringLiteral("Caps Lock"));
    game_text.setObjectName(QStringLiteral("label_header"));
    layout.addWidget(&game_text);
    keyboard.show();
    if (!check(caps.text(), u"锁定大写") || !check(space.text(), u"空格")
        || !check(game_text.text(), u"Caps Lock")) {
        return 1;
    }

    for (const auto& english : {QStringLiteral("Caps Lock Off"), QStringLiteral("Caps Lock")}) {
        caps.setText(english);
        app.processEvents();
        const auto expected = english.endsWith(QStringLiteral("Off")) ? u"解除大写锁定" : u"锁定大写";
        if (!check(caps.text(), expected)) {
            return 1;
        }
    }

    QLabel unrelated_text(QStringLiteral("Caps Lock"));
    unrelated_text.setObjectName(QStringLiteral("label_shift_shift"));
    unrelated_text.show();
    return check(unrelated_text.text(), u"Caps Lock") && checkSettingsAndAddOns() ? 0 : 1;
}

#include "test_translation.moc"

// SPDX-License-Identifier: GPL-3.0-or-later

#include <QAction>
#include <QCoreApplication>
#include <QEvent>
#include <QGenericPlugin>
#include <QLabel>
#include <QMenu>
#include <QPushButton>
#include <QStatusBar>
#include <QStringView>
#include <QTimer>

#include "other_text.h"

namespace {

struct WidgetText {
    QStringView object_name;
    QStringView english;
    QStringView chinese;
};

// qt_software_keyboard.ui (notr labels) and ChangeBottomOSKIndex() in both releases.
constexpr WidgetText keyboard_texts[] = {
    {u"label_shift", u"Shift", u"切换大写"},
    {u"label_shift_shift", u"Caps Lock", u"锁定大写"},
    {u"label_shift_shift", u"Caps Lock Off", u"解除大写锁定"},
    {u"label_cancel", u"Cancel", u"取消"},
    {u"label_cancel_shift", u"Cancel", u"取消"},
    {u"label_cancel_num", u"Cancel", u"取消"},
    {u"label_enter", u"Enter", u"确认"},
    {u"label_enter_shift", u"Enter", u"确认"},
    {u"label_enter_num", u"Enter", u"确认"},
    {u"button_return", u"Return", u"换行"},
    {u"button_return_shift", u"Return", u"换行"},
    {u"button_ok", u"OK", u"确定"},
    {u"button_ok_shift", u"OK", u"确定"},
    {u"button_ok_num", u"OK", u"确定"},
    {u"button_space", u"Space", u"空格"},
    {u"button_space_shift", u"Space", u"空格"},
};

bool inSoftwareKeyboard(const QWidget* widget) {
    for (const auto* parent = widget->parentWidget(); parent; parent = parent->parentWidget()) {
        if (parent->inherits("QtSoftwareKeyboardDialog")) {
            return true;
        }
    }
    return false;
}

void translateKeyboardText(QWidget* widget, QStringView name) {
    if (!name.startsWith(u"label_") && !name.startsWith(u"button_")) {
        return;
    }
    for (const auto& entry : keyboard_texts) {
        if (entry.object_name != name) {
            continue;
        }
        if (auto* label = qobject_cast<QLabel*>(widget); label && label->text() == entry.english
            && inSoftwareKeyboard(label)) {
            label->setText(entry.chinese.toString());
            return;
        }
        if (auto* button = qobject_cast<QPushButton*>(widget); button && button->text() == entry.english
            && inSoftwareKeyboard(button)) {
            button->setText(entry.chinese.toString());
            return;
        }
    }
}

QPushButton* mainStatusButton(QWidget* widget) {
    auto* button = qobject_cast<QPushButton*>(widget);
    if (button && button->isCheckable() && qobject_cast<QStatusBar*>(button->parentWidget())
        && button->window()->inherits("MainWindow")) {
        return button;
    }
    return nullptr;
}

// Fritchii v5.4 MainWindow status button setters and qt_common/config/shared_translation.h.
constexpr WidgetText status_texts[] = {
    {u"DockingStatusBarButton", u"DOCKED", u"主机模式"},
    {u"DockingStatusBarButton", u"HANDHELD", u"掌机模式"},
    {u"GPUStatusBarButton", u"FAST", u"快速"},
    {u"GPUStatusBarButton", u"ACCURATE", u"精确"},
    {u"TogglableStatusBarButton", u"NO AA", u"关闭抗锯齿"},
    {u"TogglableStatusBarButton", u"NEAREST", u"最近邻"},
    {u"TogglableStatusBarButton", u"BILINEAR", u"双线性"},
    {u"TogglableStatusBarButton", u"BICUBIC", u"双三次"},
    {u"TogglableStatusBarButton", u"ZERO-TANGENT", u"零切线"},
    {u"TogglableStatusBarButton", u"B-SPLINE", u"B 样条"},
    {u"TogglableStatusBarButton", u"GAUSSIAN", u"高斯"},
    {u"TogglableStatusBarButton", u"AREA", u"区域"},
};

void translateStatusButton(QWidget* widget, QStringView name) {
    if (!name.endsWith(u"StatusBarButton")) {
        return;
    }
    auto* button = mainStatusButton(widget);
    if (!button) {
        return;
    }
    const auto english = button->text();
    for (const auto& entry : status_texts) {
        if (entry.object_name == name && entry.english == english) {
            button->setText(entry.chinese.toString());
            return;
        }
    }
}

void translateDockingMenu(QMenu* menu) {
    const auto actions = menu->actions();
    if (actions.size() != 2) {
        return;
    }
    auto* docked = actions[0];
    auto* handheld = actions[1];
    if (docked->text() == QStringLiteral("Handheld")) {
        qSwap(docked, handheld);
    }
    if (docked->text() == QStringLiteral("Docked") && handheld->text() == QStringLiteral("Handheld")) {
        docked->setText(QStringLiteral("主机模式"));
        handheld->setText(QStringLiteral("掌机模式"));
    }
}

class TranslationFilter final : public QObject {
public:
    TranslationFilter() {
        QCoreApplication::instance()->installEventFilter(this);
    }

protected:
    bool eventFilter(QObject* receiver, QEvent* event) override {
        translateOtherText(receiver, event);

        const auto type = event->type();
        if (type != QEvent::Show && type != QEvent::Polish && type != QEvent::Paint
            && type != QEvent::ContextMenu) {
            return false;
        }
        auto* widget = qobject_cast<QWidget*>(receiver);
        if (!widget) {
            return false;
        }
        const auto name = widget->objectName();
        if (type == QEvent::ContextMenu) {
            if (name == QStringLiteral("DockingStatusBarButton") && mainStatusButton(widget)) {
                docking_menu_requested = true;
                QTimer::singleShot(0, this, [this] { docking_menu_requested = false; });
            }
            return false;
        }
        if (docking_menu_requested && type == QEvent::Show) {
            if (auto* menu = qobject_cast<QMenu*>(widget)) {
                translateDockingMenu(menu);
                docking_menu_requested = false;
            }
        }
        translateStatusButton(widget, name);
        translateKeyboardText(widget, name);
        return false;
    }

private:
    bool docking_menu_requested = false;
};

class TranslationPlugin final : public QGenericPlugin {
    Q_OBJECT
    Q_PLUGIN_METADATA(IID QGenericPluginFactoryInterface_iid FILE "translation.json")

public:
    QObject* create(const QString& key, const QString&) override {
        return key.compare(QStringLiteral("afei-translation"), Qt::CaseInsensitive) == 0
                   ? new TranslationFilter()
                   : nullptr;
    }
};

} // namespace

#include "translation.moc"

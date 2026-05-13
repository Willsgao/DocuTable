"""
设置管理模块
处理API配置、参数设置等功能
"""
import traceback

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit,
    QSpinBox, QPushButton, QGroupBox, QFormLayout, QMessageBox
)
from PyQt5.QtCore import Qt

from codes.pdf_extractor import save_config, VisionLLM


class SettingsManager:
    """设置管理器"""

    def __init__(self, main_window):
        self.mw = main_window

    def toggle_key_visibility(self):
        """切换Key可见性"""
        if self.mw.api_key_input.echoMode() == QLineEdit.Password:
            self.mw.api_key_input.setEchoMode(QLineEdit.Normal)
            self.mw.show_key_btn.setText("🔒")
        else:
            self.mw.api_key_input.setEchoMode(QLineEdit.Password)
            self.mw.show_key_btn.setText("👁")

    def test_api(self):
        """测试API连接"""
        try:
            self.mw.test_api_btn.setEnabled(False)
            self.mw.test_api_btn.setText("测试中...")

            api_key = self.mw.api_key_input.text().strip()
            endpoint = self.mw.endpoint_input.text().strip()
            model = self.mw.model_input.text().strip()

            if not api_key or not endpoint or not model:
                raise ValueError("请填写完整的API信息")

            llm = VisionLLM(api_key, endpoint, model)
            success, message = llm.test_connection()

            if success:
                QMessageBox.information(self.mw, "连接成功", message)
            else:
                QMessageBox.warning(self.mw, "连接失败", message)
        except Exception as e:
            QMessageBox.critical(self.mw, "连接失败", f"连接失败:\n{traceback.format_exc()}")
        finally:
            self.mw.test_api_btn.setEnabled(True)
            self.mw.test_api_btn.setText("🧪 测试连接")

    def save_settings(self):
        """保存设置"""
        self.mw.config["doubao_api_key"] = self.mw.api_key_input.text().strip()
        self.mw.config["doubao_endpoint"] = self.mw.endpoint_input.text().strip()
        self.mw.config["doubao_model"] = self.mw.model_input.text().strip()
        self.mw.config["max_pages"] = self.mw.max_pages_spin.value()
        version_map = {"v1（位置分析+pdfplumber混合）": "v1", "v2（表格线+对齐聚簇）": "v2"}
        self.mw.config["extraction_version"] = version_map.get(
            self.mw.extraction_version_combo.currentText(), "v2"
        )

        save_config(self.mw.config)
        # 更新状态栏版本标签
        version_map_ui = {"v1": "V1", "v2": "V2"}
        saved_ver = self.mw.config.get("extraction_version", "v2")
        self.mw.extractor_version_label.setText(
            f"提取器: <b>{version_map_ui.get(saved_ver, saved_ver.upper())}</b>"
        )
        self.mw.status_bar.showMessage("配置已保存")
        QMessageBox.information(self.mw, "成功", "配置已保存！下次点击「开始处理」时生效。")


class SettingsDialog(QDialog):
    """设置对话框（保留兼容）"""

    def __init__(self, parent, config):
        super().__init__(parent)
        self.config = config
        self.init_ui()

    def init_ui(self):
        """初始化UI"""
        self.setWindowTitle("设置")
        self.setMinimumWidth(500)
        self.setModal(True)

        layout = QVBoxLayout(self)

        # API设置组
        api_group = QGroupBox("API设置")
        api_layout = QFormLayout(api_group)

        self.api_key_edit = QLineEdit(self.config.get("doubao_api_key", ""))
        api_layout.addRow("豆包API Key:", self.api_key_edit)

        self.endpoint_edit = QLineEdit(self.config.get("doubao_endpoint", ""))
        api_layout.addRow("推理接入点:", self.endpoint_edit)

        self.model_edit = QLineEdit(self.config.get("doubao_model", ""))
        api_layout.addRow("模型名称:", self.model_edit)

        layout.addWidget(api_group)

        # 按钮
        btn_layout = QHBoxLayout()
        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    def get_settings(self):
        """获取设置"""
        return {
            "doubao_api_key": self.api_key_edit.text().strip(),
            "doubao_endpoint": self.endpoint_edit.text().strip(),
            "doubao_model": self.model_edit.text().strip(),
        }

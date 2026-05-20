# -*- coding: utf-8 -*-
"""
工具模块 - 缓存、配置、历史记录管理
"""

import os
import sys
import json
import shutil
import tempfile
import hashlib
from pathlib import Path
from datetime import datetime


# ============================================================
# 临时文件管理 - 避免占用C盘
# ============================================================
def get_temp_dir():
    """获取临时目录，优先使用D盘或E盘"""
    for drive in ['D:', 'E:', 'F:']:
        temp_base = os.path.join(drive, 'temp', 'pdf_extractor')
        try:
            os.makedirs(temp_base, exist_ok=True)
            test_file = os.path.join(temp_base, '.test')
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
            return temp_base
        except:
            continue
    return tempfile.gettempdir()


TEMP_DIR = get_temp_dir()
print(f"临时文件目录: {TEMP_DIR}")


def cleanup_temp_files():
    """清理临时文件"""
    if os.path.exists(TEMP_DIR):
        try:
            for item in os.listdir(TEMP_DIR):
                item_path = os.path.join(TEMP_DIR, item)
                try:
                    if os.path.isfile(item_path):
                        os.remove(item_path)
                    elif os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                except:
                    pass
        except:
            pass


# ============================================================
# 项目根目录 - 向上两级到达项目根目录
# ============================================================
def get_project_root():
    """获取项目根目录（兼容开发模式和 PyInstaller 打包模式）"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包模式：exe 所在目录
        return Path(sys.executable).parent
    else:
        # 开发模式：向上两级到达项目根目录
        return Path(__file__).parent.parent.parent


def get_config_dir():
    """获取配置目录"""
    return get_project_root() / "config"


def get_config_file():
    """获取配置文件路径"""
    return get_config_dir() / "settings.json"


def get_mid_data_dir():
    """获取中间数据目录"""
    return get_project_root() / "data" / "mid_cache"


def get_history_file():
    """获取历史记录文件路径"""
    return get_project_root() / "pdf_history.json"


# 确保目录存在（parents=True 支持多层目录创建）
get_config_dir().mkdir(parents=True, exist_ok=True)
get_mid_data_dir().mkdir(parents=True, exist_ok=True)


# ============================================================
# 按文件管理的缓存目录
# ============================================================
def get_pdf_cache_dir(pdf_path):
    """获取PDF文件对应的独立缓存目录：data/mid_cache/<pdf文件名>/"""
    pdf_name = Path(pdf_path).stem[:100]
    # 移除Windows文件名非法字符: \ / : * ? " < > |
    for ch in ('\\', '/', ':', '*', '?', '"', '<', '>', '|'):
        pdf_name = pdf_name.replace(ch, '_')
    # strip 首尾空格和点号，Windows 不允许末尾空格/点号
    pdf_name = pdf_name.strip().rstrip('.')
    # 处理文件名乱码或无效字符：仅保留中文、英文、数字、下划线、连字符
    sanitized = ''.join(c if c == '_' or c == '-' or c.isalnum() or '\u4e00' <= c <= '\u9fff' else '_' for c in pdf_name)
    # 如果 sanitize 后为空或与原文件名差异很大（乱码），用文件 hash 作为目录名
    if not sanitized.strip('_'):
        file_hash = get_pdf_file_hash(pdf_path)
        sanitized = file_hash[:16]
    return get_mid_data_dir() / sanitized


def get_pdf_preview_dir(pdf_path):
    """获取PDF文件对应的预览图目录"""
    return get_pdf_cache_dir(pdf_path) / "preview"


def get_cache_file_path(pdf_path):
    """根据PDF文件路径生成缓存文件路径：data/mid_cache/<pdf文件名>/data.json"""
    return get_pdf_cache_dir(pdf_path) / "data.json"


def get_pdf_file_hash(pdf_path):
    """获取PDF文件的MD5哈希值"""
    with open(pdf_path, 'rb') as f:
        head = f.read(1024 * 1024)
        f.seek(max(0, os.path.getsize(pdf_path) - 1024 * 1024))
        tail = f.read()
        return hashlib.md5(head + tail).hexdigest()


def save_mid_data(pdf_path, data):
    """保存中间数据到缓存"""
    CACHE_VERSION = 2
    cache_file = get_cache_file_path(pdf_path)

    # 确保缓存目录存在
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    cache_data = {
        'cache_version': CACHE_VERSION,
        'pdf_info': {
            'path': os.path.normpath(pdf_path),
            'modified_time': datetime.fromtimestamp(os.path.getmtime(pdf_path)).isoformat(),
            'file_hash': get_pdf_file_hash(pdf_path),
            'file_size': os.path.getsize(pdf_path),
            'cached_time': datetime.now().isoformat()
        },
        'data': data
    }

    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump(cache_data, f, ensure_ascii=False, indent=2)
    print(f"[CACHE] 中间数据已保存: {cache_file}")
    return cache_file


def load_mid_data(pdf_path):
    """从缓存加载中间数据"""
    CACHE_VERSION = 2
    cache_file = get_cache_file_path(pdf_path)

    if not cache_file.exists():
        return None

    try:
        with open(cache_file, 'r', encoding='utf-8') as f:
            cache_data = json.load(f)

        if cache_data.get('cache_version', 1) < CACHE_VERSION:
            print(f"[CACHE] 缓存版本过旧，需要重新解析")
            return None

        if os.path.exists(pdf_path):
            if get_pdf_file_hash(pdf_path) != cache_data.get('pdf_info', {}).get('file_hash', ''):
                print(f"[CACHE] PDF文件已修改，需要重新解析")
                return None

            current_size = os.path.getsize(pdf_path)
            cached_size = cache_data.get('pdf_info', {}).get('file_size', 0)
            if current_size != cached_size:
                print(f"[CACHE] PDF文件大小已变化，需要重新解析")
                return None
        else:
            return None

        print(f"[CACHE] 从缓存加载: {cache_file}")
        return cache_data.get('data')
    except Exception as e:
        print(f"[CACHE] 加载缓存失败: {e}")
    return None


def is_cache_valid(pdf_path):
    """检查缓存是否有效"""
    cache_file = get_cache_file_path(pdf_path)
    if not cache_file.exists():
        return False

    try:
        with open(cache_file, 'r', encoding='utf-8') as f:
            cache_data = json.load(f)

        if not os.path.exists(pdf_path):
            return False

        current_hash = get_pdf_file_hash(pdf_path)
        cached_hash = cache_data.get('pdf_info', {}).get('file_hash', '')

        if current_hash != cached_hash:
            return False

        current_size = os.path.getsize(pdf_path)
        cached_size = cache_data.get('pdf_info', {}).get('file_size', 0)

        return current_size == cached_size
    except:
        return False


def get_cached_pdf_info(pdf_path):
    """获取缓存的PDF信息"""
    cache_file = get_cache_file_path(pdf_path)
    if cache_file.exists():
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)

            mtime = datetime.fromtimestamp(cache_file.stat().st_mtime)
            pdf_info = cache_data.get('pdf_info', {})

            return {
                'exists': True,
                'path': str(cache_file),
                'modified': mtime,
                'size': cache_file.stat().st_size,
                'pdf_modified': pdf_info.get('modified_time', ''),
                'cached_time': pdf_info.get('cached_time', ''),
                'pdf_size': pdf_info.get('file_size', 0),
                'is_valid': is_cache_valid(pdf_path)
            }
        except:
            pass
    return {'exists': False, 'is_valid': False}


def get_all_cached_files():
    """获取所有缓存文件的信息列表（扫描子文件夹）"""
    cached_files = []
    for folder in get_mid_data_dir().iterdir():
        if not folder.is_dir():
            continue
        cache_file = folder / "data.json"
        if not cache_file.exists():
            continue
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)

            pdf_info = cache_data.get('pdf_info', {})
            mtime = datetime.fromtimestamp(cache_file.stat().st_mtime)
            data = cache_data.get('data', {})
            tables = data.get('tables', [])

            cached_files.append({
                'cache_file': str(cache_file),
                'cache_time': mtime,
                'pdf_path': pdf_info.get('path', '未知'),
                'pdf_modified': pdf_info.get('modified_time', ''),
                'cached_time': pdf_info.get('cached_time', ''),
                'pdf_size': pdf_info.get('file_size', 0),
                'cache_size': cache_file.stat().st_size,
                'tables_count': len(tables),
                'is_valid': is_cache_valid(pdf_info.get('path', ''))
            })
        except:
            continue

    cached_files.sort(key=lambda x: x['cache_time'], reverse=True)
    return cached_files


def delete_cache_file(pdf_path):
    """删除指定PDF的整个缓存目录"""
    cache_dir = get_pdf_cache_dir(pdf_path)
    if cache_dir.exists():
        try:
            shutil.rmtree(cache_dir)
            print(f"[CACHE] 已删除缓存目录: {cache_dir}")
            return True
        except Exception as e:
            print(f"[CACHE] 删除缓存目录失败: {e}")
    return False


# ============================================================
# 配置文件管理
# ============================================================
def load_config():
    """加载配置文件"""
    config_file = get_config_file()
    if config_file.exists():
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {
        "doubao_api_key": "",
        "doubao_endpoint": "ark.cn-beijing.volces.com",
        "doubao_model": "doubao-pro-32k",
        "auto_detect_mode": True,
        "max_pages": 500,
        "extraction_version": "v2"
    }


def save_config(config):
    """保存配置文件"""
    with open(get_config_file(), 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


# ============================================================
# 历史记录管理
# ============================================================
def load_pdf_history():
    """加载PDF解析历史记录"""
    history_file = get_history_file()
    if history_file.exists():
        try:
            with open(history_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return []


def save_pdf_history(history):
    """保存PDF解析历史记录"""
    with open(get_history_file(), 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

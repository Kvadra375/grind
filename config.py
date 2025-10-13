"""
Конфигурационный файл для Hybrid Chart
Все настройки приложения вынесены в этот файл для удобства управления
"""

import os
from pathlib import Path

# Базовые настройки приложения
APP_CONFIG = {
    'name': 'Hybrid Price Chart',
    'version': '1.0.0',
    'author': 'EugenChart',
    'description': 'Мониторинг цен криптовалют с поддержкой CEX и DEX'
}

# Настройки логирования
LOGGING_CONFIG = {
    'level': 'INFO',
    'console_level': 'INFO',
    'file_level': 'DEBUG',
    'max_file_size': 5 * 1024 * 1024,  # 5MB
    'backup_count': 3,
    'log_dir': 'logs',
    'files': {
        'main': 'hybrid_chart.log',
        'websocket': 'websocket.log',
        'click_debug': 'click_debug.log'
    }
}

# Настройки графиков
CHART_CONFIG = {
    'max_data_points': 1000,  # Максимум точек на графике
    'memory_cleanup_interval': 60,  # Интервал очистки памяти (секунды)
    'cleanup_threshold': 50,  # Количество точек для периодической очистки
    'animation_interval': 1000,  # Интервал анимации (миллисекунды)
    'default_figure_size': (6, 4),
    'theme': 'dark_background'
}

# Настройки WebSocket
WEBSOCKET_CONFIG = {
    'max_retry_attempts': 5,
    'retry_delay': 5,  # Базовая задержка (секунды)
    'ping_interval': 30,  # Интервал ping (секунды)
    'connection_timeout': 10,  # Таймаут подключения (секунды)
    'mexc': {
        'url': 'wss://contract.mexc.com/ws',
        'ping_message': 'ping'
    }
}

# Настройки API
API_CONFIG = {
    'timeout': 10,  # Таймаут запросов (секунды)
    'max_retries': 3,
    'retry_delay': 1,  # Задержка между попытками (секунды)
    'okx': {
        'base_url': 'https://www.okx.com',
        'price_endpoint': '/api/v5/market/ticker'
    }
}

# Настройки мониторинга
MONITORING_CONFIG = {
    'default_spread_threshold': 5.0,  # Порог спреда по умолчанию (%)
    'default_monitor_interval': 2.0,  # Интервал мониторинга (секунды)
    'auto_open_charts': True,
    'disable_alerts': False,
    'max_concurrent_charts': 10  # Максимум одновременных графиков
}

# Настройки GUI
GUI_CONFIG = {
    'window_size': '1000x700',
    'background_color': '#000000',
    'theme': 'dark',
    'font_family': 'Arial',
    'font_size': 10,
    'colors': {
        'primary': '#00ff00',
        'secondary': '#ff0000',
        'background': '#000000',
        'text': '#ffffff',
        'grid': '#333333'
    }
}

# Настройки файлов
FILES_CONFIG = {
    'tokens_file': 'tokens.json',
    'blacklist_file': 'blacklist.json',
    'settings_file': 'chart_settings.json',
    'backup_dir': 'backups',
    'auto_backup': True,
    'backup_interval': 3600  # Интервал резервного копирования (секунды)
}

# Настройки безопасности
SECURITY_CONFIG = {
    'validate_addresses': True,
    'max_price_value': 1000000,  # Максимальное значение цены
    'min_price_value': 0.00000001,  # Минимальное значение цены
    'max_token_name_length': 50,
    'sanitize_input': True
}

# Поддерживаемые блокчейны
SUPPORTED_CHAINS = {
    'ethereum': {
        'name': 'Ethereum',
        'symbol': 'ETH',
        'address_pattern': r'^0x[a-fA-F0-9]{40}$',
        'explorer': 'https://etherscan.io/token/'
    },
    'bsc': {
        'name': 'Binance Smart Chain',
        'symbol': 'BNB',
        'address_pattern': r'^0x[a-fA-F0-9]{40}$',
        'explorer': 'https://bscscan.com/token/'
    },
    'solana': {
        'name': 'Solana',
        'symbol': 'SOL',
        'address_pattern': r'^[1-9A-HJ-NP-Za-km-z]{32,44}$',
        'explorer': 'https://solscan.io/token/'
    },
    'polygon': {
        'name': 'Polygon',
        'symbol': 'MATIC',
        'address_pattern': r'^0x[a-fA-F0-9]{40}$',
        'explorer': 'https://polygonscan.com/token/'
    },
    'arbitrum': {
        'name': 'Arbitrum',
        'symbol': 'ARB',
        'address_pattern': r'^0x[a-fA-F0-9]{40}$',
        'explorer': 'https://arbiscan.io/token/'
    },
    'base': {
        'name': 'Base',
        'symbol': 'BASE',
        'address_pattern': r'^0x[a-fA-F0-9]{40}$',
        'explorer': 'https://basescan.org/token/'
    },
    'optimism': {
        'name': 'Optimism',
        'symbol': 'OP',
        'address_pattern': r'^0x[a-fA-F0-9]{40}$',
        'explorer': 'https://optimistic.etherscan.io/token/'
    }
}

# Настройки производительности
PERFORMANCE_CONFIG = {
    'enable_monitoring': True,
    'monitor_interval': 30,  # Интервал мониторинга производительности (секунды)
    'memory_warning_threshold': 100 * 1024 * 1024,  # 100MB
    'cpu_warning_threshold': 80,  # 80%
    'gc_threshold': 1000  # Количество объектов для принудительной сборки мусора
}

def get_config():
    """Получить полную конфигурацию"""
    return {
        'app': APP_CONFIG,
        'logging': LOGGING_CONFIG,
        'chart': CHART_CONFIG,
        'websocket': WEBSOCKET_CONFIG,
        'api': API_CONFIG,
        'monitoring': MONITORING_CONFIG,
        'gui': GUI_CONFIG,
        'files': FILES_CONFIG,
        'security': SECURITY_CONFIG,
        'chains': SUPPORTED_CHAINS,
        'performance': PERFORMANCE_CONFIG
    }

def validate_config():
    """Валидация конфигурации"""
    config = get_config()
    
    # Проверяем обязательные директории
    log_dir = Path(config['logging']['log_dir'])
    log_dir.mkdir(exist_ok=True)
    
    backup_dir = Path(config['files']['backup_dir'])
    backup_dir.mkdir(exist_ok=True)
    
    return True

# Инициализация конфигурации
if __name__ == "__main__":
    validate_config()
    print("Configuration validated successfully!")







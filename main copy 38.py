import websocket
import json
import threading
import time
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from datetime import datetime
import numpy as np
import logging
import requests
from bs4 import BeautifulSoup
import re
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import os
from ctypes import windll, byref, c_int
import queue
import pyperclip

# Настройка логирования
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Отдельный логгер для отладки кликов
click_logger = logging.getLogger('click_debug')
click_handler = logging.FileHandler('logs/click_debug.log', mode='w', encoding='utf-8')
click_handler.setLevel(logging.DEBUG)
click_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
click_handler.setFormatter(click_formatter)
click_logger.addHandler(click_handler)
click_logger.setLevel(logging.DEBUG)
click_logger.propagate = False  # Не передавать в основной логгер

# Windows: включить тёмную тему заголовка окна (кнопки свернуть/развернуть/закрыть)
def enable_dark_title_bar(tk_window):
    if os.name != 'nt':
        return
    try:
        hwnd = tk_window.winfo_id()
        use_dark = c_int(1)
        # 20 — Windows 10 2004+/11, 19 — Windows 10 1809+
        for attr in (20, 19):
            windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, byref(use_dark), 4)
    except Exception as e:
        logger.error(f"Failed to enable dark title bar: {e}")

class HybridChart:
    def __init__(self, parent_window=None):
        # Данные для графика (как было)
        self.times = []
        self.cex_prices = []
        self.dex_prices = []
        
        # Реальные данные
        self.mexc_price = None
        self.dex_price = None
        
        # WebSocket соединения
        self.ws_mexc = None
        
        # Флаг для остановки
        self.running = True
        
        # Родительское окно
        self.parent_window = parent_window
        
        # Настройка matplotlib
        plt.style.use('dark_background')
        self.fig, self.ax = plt.subplots(figsize=(6, 4))
        # Тёмный фон для фигуры и области графика
        self.fig.patch.set_facecolor('#000000')
        self.ax.set_facecolor('#000000')
        
        # Включаем интерактивные возможности
        self.fig.canvas.mpl_connect('scroll_event', self.on_scroll)
        self.fig.canvas.mpl_connect('button_press_event', self.on_press)
        self.fig.canvas.mpl_connect('button_release_event', self.on_release)
        self.fig.canvas.mpl_connect('motion_notify_event', self.on_motion)
        
        # Переменные для панорамирования
        self.pan_start = None
        self.pan_active = False
        
        # Флаг для отключения автоматического масштабирования
        self.manual_zoom = False
        self.manual_xlim = None
        self.manual_ylim = None
        
        # Настройка графика (заголовок будет обновлен в start)
        self.ax.set_title('Loading...', color='white', fontsize=16, fontweight='bold')
        self.ax.set_xlabel('Time', color='white', fontsize=12)
        self.ax.set_ylabel('Price', color='white', fontsize=12)
        self.ax.tick_params(colors='white', labelsize=10)
        # Разместить цену (ось Y) справа
        self.ax.yaxis.tick_right()
        self.ax.yaxis.set_label_position('right')
        self.ax.spines['right'].set_color('white')
        self.ax.spines['left'].set_visible(False)
        self.ax.spines['top'].set_visible(False)
        self.ax.spines['bottom'].set_color('white')
        self.ax.grid(True, alpha=0.2, linestyle='-', linewidth=0.5)
        
        # Инициализация линий с более контрастными цветами
        self.line_cex, = self.ax.plot([], [], color='#00FF00', linewidth=3, label='CEX Price (MEXC Futures)', alpha=0.9)
        self.line_dex, = self.ax.plot([], [], color='#FF0000', linewidth=3, label='DEX Price (OKX)', alpha=0.9)
        
        # Легенда удалена - не нужна, так как цвета и так видны
        # legend = self.ax.legend(...)
        
        # Текст для спреда с улучшенным стилем
        self.spread_text = self.ax.text(0.02, 0.02, '', transform=self.ax.transAxes, 
                                       color='white', fontsize=14, fontweight='bold',
                                       bbox=dict(boxstyle="round,pad=0.5", facecolor='black', alpha=0.8, edgecolor='white'))
        
        # Переменная для заливки
        self.fill_cex = None

        # Метки цен на правой оси для CEX и DEX
        self.cex_price_label = self.ax.text(
            1.01, 0, '', transform=self.ax.get_yaxis_transform(), va='center', ha='left',
            color='white', fontsize=10, clip_on=False, zorder=10,
            bbox=dict(boxstyle='round,pad=0.2', facecolor='#154c1d', alpha=0.95, edgecolor='none')
        )
        self.dex_price_label = self.ax.text(
            1.01, 0, '', transform=self.ax.get_yaxis_transform(), va='center', ha='left',
            color='white', fontsize=10, clip_on=False, zorder=10,
            bbox=dict(boxstyle='round,pad=0.2', facecolor='#6b1f1f', alpha=0.95, edgecolor='none')
        )

        # Горизонтальные направляющие линии под метки
        self.cex_guide = self.ax.axhline(y=np.nan, color='#00FF00', linestyle='--', alpha=0.5, linewidth=2, zorder=3)
        self.dex_guide = self.ax.axhline(y=np.nan, color='#FF0000', linestyle='--', alpha=0.5, linewidth=2, zorder=3)

        # Маркеры отключены
        self.cex_marker = None
        self.dex_marker = None

        # Бейджи прямо у правой границы оси (дублируют текст и фиксируются к правому краю)
        # Используем размер по умолчанию badge_width = 0.11
        default_pad = 0.11 * 0.5  # pad_size = width * 0.5
        default_font = 0.11 * 60  # font_size = width * 60
        self.cex_badge = self.ax.annotate('', xy=(0, 0), xytext=(8, 0), textcoords='offset points',
                                          ha='left', va='center', color='white', zorder=11, clip_on=False,
                                          fontsize=default_font,
                                          bbox=dict(boxstyle=f'round,pad={default_pad}', fc='#1e7f2e', ec='none', alpha=0.98))
        self.dex_badge = self.ax.annotate('', xy=(0, 0), xytext=(8, 0), textcoords='offset points',
                                          ha='left', va='center', color='white', zorder=11, clip_on=False,
                                          fontsize=default_font,
                                          bbox=dict(boxstyle=f'round,pad={default_pad}', fc='#9e2e2e', ec='none', alpha=0.98))
        
        logger.info("Hybrid chart initialized")
    
    def on_scroll(self, event):
        """Обработка прокрутки колесика мыши для масштабирования"""
        if event.inaxes != self.ax:
            return
        
        # Определяем направление прокрутки
        if event.button == 'up':
            # Приближение
            scale_factor = 0.8
        elif event.button == 'down':
            # Отдаление
            scale_factor = 1.2
        else:
            return
        
        # Получаем текущие границы
        xlim = self.ax.get_xlim()
        ylim = self.ax.get_ylim()
        
        # Вычисляем центр масштабирования
        x_center = event.xdata if event.xdata else (xlim[0] + xlim[1]) / 2
        y_center = event.ydata if event.ydata else (ylim[0] + ylim[1]) / 2
        
        # Применяем масштабирование
        x_range = (xlim[1] - xlim[0]) * scale_factor
        y_range = (ylim[1] - ylim[0]) * scale_factor
        
        new_xlim = [x_center - x_range/2, x_center + x_range/2]
        new_ylim = [y_center - y_range/2, y_center + y_range/2]
        
        # Если у нас есть данные, убеждаемся что правая граница не выходит за пределы данных
        if len(self.times) > 0:
            max_time = max(self.times)
            # Если правая граница выходит за пределы данных, корректируем её
            if new_xlim[1] > max_time:
                # Сдвигаем окно так, чтобы правая граница была на последней точке данных
                time_diff = new_xlim[1] - new_xlim[0]
                new_xlim[1] = max_time
                new_xlim[0] = max_time - time_diff
        
        # Устанавливаем новые границы
        self.ax.set_xlim(new_xlim)
        self.ax.set_ylim(new_ylim)
        
        # Сохраняем ручные границы
        self.manual_zoom = True
        self.manual_xlim = new_xlim
        self.manual_ylim = new_ylim
        
        # Обновляем график
        self.fig.canvas.draw()
    
    def on_press(self, event):
        """Обработка нажатия кнопки мыши для начала панорамирования"""
        if event.inaxes != self.ax:
            return
        
        if event.button == 1:  # Левая кнопка мыши
            self.pan_start = (event.xdata, event.ydata)
            self.pan_active = True
    
    def on_release(self, event):
        """Обработка отпускания кнопки мыши"""
        self.pan_active = False
        self.pan_start = None
    
    def on_motion(self, event):
        """Обработка движения мыши для панорамирования"""
        if not self.pan_active or not self.pan_start or event.inaxes != self.ax:
            return
        
        if event.xdata is None or event.ydata is None:
            return
        
        # Вычисляем смещение
        dx = event.xdata - self.pan_start[0]
        dy = event.ydata - self.pan_start[1]
        
        # Получаем текущие границы
        xlim = self.ax.get_xlim()
        ylim = self.ax.get_ylim()
        
        # Применяем смещение
        new_xlim = [xlim[0] - dx, xlim[1] - dx]
        new_ylim = [ylim[0] - dy, ylim[1] - dy]
        
        # Ограничиваем панорамирование границами данных
        if len(self.times) > 0:
            min_time = min(self.times)
            max_time = max(self.times)
            
            # Если левая граница выходит за пределы данных, корректируем
            if new_xlim[0] < min_time:
                time_diff = new_xlim[1] - new_xlim[0]
                new_xlim[0] = min_time
                new_xlim[1] = min_time + time_diff
            
            # Если правая граница выходит за пределы данных, корректируем
            if new_xlim[1] > max_time:
                time_diff = new_xlim[1] - new_xlim[0]
                new_xlim[1] = max_time
                new_xlim[0] = max_time - time_diff
        
        # Устанавливаем новые границы
        self.ax.set_xlim(new_xlim)
        self.ax.set_ylim(new_ylim)
        
        # Сохраняем ручные границы
        self.manual_zoom = True
        self.manual_xlim = new_xlim
        self.manual_ylim = new_ylim
        
        # Обновляем начальную точку
        self.pan_start = (event.xdata, event.ydata)
        
        # Обновляем график
        self.fig.canvas.draw()
    
    def reset_zoom(self):
        """Сброс масштаба к автоматическому"""
        self.manual_zoom = False
        self.manual_xlim = None
        self.manual_ylim = None
        self.ax.relim()
        self.ax.autoscale_view()
        self.fig.canvas.draw()
    
    def connect_mexc(self, token_symbol):
        """Подключение к MEXC Futures WebSocket (из working_real_chart.py)"""
        # Добавляем уникальный идентификатор для этого соединения
        self.ws_id = f"{token_symbol}_{id(self)}"
        
        def on_message(ws, message):
            try:
                data = json.loads(message)
                
                # Обработка тикера (раз в ~1s)
                if data.get("channel") == "push.ticker" and "data" in data:
                    ticker_data = data["data"]
                    symbol_name = f"{token_symbol}_USDT"
                    if ticker_data.get("symbol") == symbol_name and "lastPrice" in ticker_data:
                        price = float(ticker_data["lastPrice"])
                        self.mexc_price = price
                        # Немедленно обновляем GUI
                        if hasattr(self, 'fig') and self.fig:
                            self.fig.canvas.draw_idle()
                        logger.debug(f"MEXC Futures Price: {price}")
                # Обработка сделок (tick-by-tick)
                elif data.get("channel") == "push.deal" and "data" in data:
                    deals = data["data"]
                    # формат обычно массив сделок; берём последнюю
                    if isinstance(deals, list) and len(deals) > 0:
                        last = deals[-1]
                        # возможные ключи: price / p
                        p = last.get("price") or last.get("p")
                        s = last.get("symbol") or last.get("s")
                        symbol_name = f"{token_symbol}_USDT"
                        if p is not None and (s is None or s == symbol_name):
                            try:
                                price = float(p)
                                old_price = self.mexc_price
                                self.mexc_price = price
                                # Немедленно обновляем GUI при каждой сделке
                                if hasattr(self, 'fig') and self.fig:
                                    self.fig.canvas.draw_idle()
                                # Логируем только при изменении цены
                                if old_price != price:
                                    logger.debug(f"MEXC Deal Price: {price} (change: {price - old_price if old_price else 0})")
                            except Exception:
                                pass
                
                # Обработка pong ответа
                elif data.get("method") == "pong":
                    pass
                    
            except Exception as e:
                logger.error(f"MEXC message error: {e}")
        
        def on_error(ws, error):
            logger.error(f"MEXC WebSocket error: {error}")
        
        def on_close(ws, close_status_code, close_msg):
            logger.debug("MEXC WebSocket connection closed")
            if self.running:
                threading.Timer(5.0, lambda: self.connect_mexc(token_symbol)).start()
        
        def on_open(ws):
            logger.debug(f"MEXC Futures WebSocket connected for {token_symbol} (ID: {self.ws_id})")
            symbol_name = f"{token_symbol}_USDT"
            subscribe_msg = {
                "method": "sub.ticker",
                "param": {
                    "symbol": symbol_name
                }
            }
            ws.send(json.dumps(subscribe_msg))
            logger.info(f"Subscribed to {symbol_name} ticker")
            # Дополнительно подписываемся на сделки (tick-by-tick)
            subscribe_deal = {
                "method": "sub.deal",
                "param": {
                    "symbol": symbol_name
                }
            }
            try:
                ws.send(json.dumps(subscribe_deal))
                logger.info(f"Subscribed to {symbol_name} deals")
            except Exception as e:
                logger.error(f"Subscribe deal error: {e}")
        
        def ping_loop():
            while self.running and self.ws_mexc:
                try:
                    ping_msg = {"method": "ping"}
                    self.ws_mexc.send(json.dumps(ping_msg))
                    time.sleep(10)
                except Exception as e:
                    logger.debug(f"MEXC ping error: {e}")
                    break
        
        def run_websocket():
            self.ws_mexc = websocket.WebSocketApp(
                "wss://contract.mexc.com/edge",
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
                on_open=on_open
            )
            threading.Thread(target=ping_loop, daemon=True).start()
            self.ws_mexc.run_forever()
        
        threading.Thread(target=run_websocket, daemon=True).start()
    
    def parse_okx_price(self, token_address, chain_hint=None):
        """Парсинг цены с OKX Web3. Поддержка chain_hint: ethereum | bsc | solana | base | arbitrum | polygon"""
        try:
            # Определяем блокчейн
            chain = None
            if chain_hint:
                ch = chain_hint.strip().lower()
                # нормализуем известные варианты
                mapping = {
                    'ethereum': 'ethereum', 'eth': 'ethereum', 'erc20': 'ethereum',
                    'bsc': 'bsc', 'bep20': 'bsc', 'binance-smart-chain': 'bsc',
                    'sol': 'solana', 'solana': 'solana',
                    'base': 'base',
                    'arbitrum': 'arbitrum', 'arbitrum_one': 'arbitrum', 'arbitrum one': 'arbitrum',
                    'polygon': 'polygon', 'matic': 'polygon'
                }
                chain = mapping.get(ch)
            if chain is None:
                # эвристика по адресу
                if len(token_address) == 44:
                    chain = 'solana'
                elif token_address.startswith('0x'):
                    # по умолчанию для EVM ставим bsc если не указан chain
                    chain = 'bsc'
                else:
                    chain = 'bsc'
            
            okx_url = f"https://web3.okx.com/ru/token/{chain}/{token_address}"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            }
            
            response = requests.get(okx_url, headers=headers, timeout=15)
            if response.status_code == 200 and response.text:
                soup = BeautifulSoup(response.content, 'html.parser')
                
                # Ищем цену в различных элементах страницы
                price_selectors = [
                    '.token-price',
                    '.price-value', 
                    '[data-testid="token-price"]',
                    '.token-price-value',
                    '.price',
                    '.token-info-price',
                    '.price-display'
                ]
                
                for selector in price_selectors:
                    price_elem = soup.select_one(selector)
                    if price_elem:
                        price_text = price_elem.get_text().strip()
                        # Извлекаем число из текста
                        price_match = re.search(r'[\d,]+\.?\d*', price_text.replace(',', ''))
                        if price_match:
                            price = float(price_match.group().replace(',', ''))
                            if price > 0:
                                logger.debug(f"OKX Price ({chain.upper()}): {price}")
                                return price
                
                # Поиск в title страницы
                if soup.title:
                    title_text = soup.title.string
                    match = re.search(r'\$([0-9,.]+)', title_text)
                    if match:
                        price = float(match.group(1).replace(',', '.'))
                        logger.debug(f"OKX Price ({chain.upper()}) from title: {price}")
                        return price
                
                # Поиск по regex в тексте страницы
                page_text = response.text
                price_patterns = [
                    r'\$?(\d{1,3}(?:,\d{3})*(?:\.\d+)?)',
                    r'(\d+\.\d+)',
                    r'(\d+,\d+)'
                ]
                
                for pattern in price_patterns:
                    matches = re.findall(pattern, page_text)
                    for match in matches:
                        try:
                            price = float(match.replace(',', ''))
                            if 0.000001 < price < 1000000:  # Разумный диапазон цен
                                logger.debug(f"OKX Price ({chain.upper()}) from regex: {price}")
                                return price
                        except ValueError:
                            continue
                
                logger.warning(f"Could not find price on OKX page for {chain}")
                return None
            else:
                logger.warning(f"OKX request failed: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"OKX parse error: {e}")
            return None
    
    def connect_dex(self, token_address, chain_hint=None):
        """Подключение к OKX Web3 для получения цены. chain_hint может указывать сеть: ethereum/bsc/solana/..."""
        def poll_dex_price():
            while self.running:
                try:
                    price = self.parse_okx_price(token_address, chain_hint=chain_hint)
                    if price and price != self.dex_price:  # Обновляем только при изменении
                        self.dex_price = price
                        # Немедленно обновляем GUI
                        if hasattr(self, 'fig') and self.fig:
                            self.fig.canvas.draw_idle()
                    # Убираем sleep - обновляем максимально часто
                except Exception as e:
                    logger.error(f"OKX polling error: {e}")
                    time.sleep(0.01)  # Минимальная пауза только при ошибке
        
        threading.Thread(target=poll_dex_price, daemon=True).start()
    
    def animate(self, frame):
        """Анимация графика"""
        if not self.running:
            return self.line_cex, self.line_dex
        
        # Обновляем данные
        current_time = datetime.now()
        
        # Всегда добавляем время, но цены только если они доступны
        self.times.append(current_time)
        
        if self.mexc_price is not None:
            self.cex_prices.append(self.mexc_price)
        else:
            # Если цена недоступна, используем последнюю известную цену или 0
            last_price = self.cex_prices[-1] if self.cex_prices else 0
            self.cex_prices.append(last_price)
        
        if self.dex_price is not None:
            self.dex_prices.append(self.dex_price)
        else:
            # Если DEX цена недоступна, используем последнюю известную цену или 0
            last_dex_price = self.dex_prices[-1] if self.dex_prices else 0
            self.dex_prices.append(last_dex_price)
        
        # Ограничиваем количество точек (15 минут при обновлении каждую секунду = 900 точек)
        max_points = 900  # 15 минут истории
        if len(self.times) > max_points:
            self.times = self.times[-max_points:]
            self.cex_prices = self.cex_prices[-max_points:]
            self.dex_prices = self.dex_prices[-max_points:]
        
        # Обновляем график
        if len(self.times) > 0:
            # Убеждаемся, что все массивы имеют одинаковую длину
            min_length = min(len(self.times), len(self.cex_prices), len(self.dex_prices))
            if min_length == 0:
                return self.line_cex, self.line_dex
            
            # Обрезаем массивы до минимальной длины
            times_trimmed = self.times[-min_length:]
            cex_prices_trimmed = self.cex_prices[-min_length:]
            dex_prices_trimmed = self.dex_prices[-min_length:]
            
            times_np = np.array(times_trimmed)
            cex_prices_np = np.array(cex_prices_trimmed)
            
            # Фильтруем данные DEX по времени - используем тот же массив времени
            if len(dex_prices_trimmed) > 0:
                # Синхронизируем DEX данные с CEX временем
                dex_prices_synced = []
                for i in range(len(times_trimmed)):
                    if i < len(dex_prices_trimmed):
                        dex_prices_synced.append(dex_prices_trimmed[i])
                    else:
                        dex_prices_synced.append(dex_prices_trimmed[-1] if dex_prices_trimmed else 0)
                
                dex_times_np = times_np
                dex_prices_np = np.array(dex_prices_synced)
            else:
                dex_times_np = np.array([])
                dex_prices_np = np.array([])
            
            # Обновляем линии
            self.line_cex.set_data(times_np, cex_prices_np)
            if len(dex_times_np) > 0:
                self.line_dex.set_data(dex_times_np, dex_prices_np)
            else:
                self.line_dex.set_data([], [])
            
            # Удаляем старую заливку и создаем новую
            if self.fill_cex is not None:
                self.fill_cex.remove()
            
            # Проверяем, что массивы имеют одинаковую длину перед созданием fill_between
            if len(times_np) > 0 and len(cex_prices_np) > 0 and len(times_np) == len(cex_prices_np):
                self.fill_cex = self.ax.fill_between(times_np, cex_prices_np, alpha=0.2, color='#00FF00')
            
            # Обновляем оси только если не используется ручное масштабирование
            if not self.manual_zoom:
                # Обновляем ось X с учетом отступов
                if len(times_np) > 1:
                    x_margin = getattr(self, 'x_margin', 0.0)
                    time_range = times_np[-1] - times_np[0]
                    margin_value = time_range * x_margin
                    self.ax.set_xlim(times_np[0] - margin_value, times_np[-1] + margin_value)
                
                # Обновляем ось Y с улучшенным масштабом
                if len(cex_prices_np) > 0:
                    all_prices = [cex_prices_np]
                    if len(dex_prices_np) > 0:
                        all_prices.append(dex_prices_np)
                    
                    all_prices_combined = np.concatenate(all_prices)
                    min_price = np.min(all_prices_combined)
                    max_price = np.max(all_prices_combined)
                    
                    # Увеличиваем отступы для лучшей читаемости
                    price_range = max_price - min_price
                    if price_range > 0:
                        # Используем настройку из ползунка, если доступна
                        margin = getattr(self, 'y_margin', 0.5)
                        margin_value = price_range * margin
                    else:
                        margin_value = max_price * 0.1 if max_price > 0 else 0.1
                    
                    self.ax.set_ylim(min_price - margin_value, max_price + margin_value)
            else:
                # При ручном масштабировании проверяем, нужно ли обновить правую границу
                if len(times_np) > 0 and self.manual_xlim is not None:
                    current_xlim = self.ax.get_xlim()
                    max_time = max(times_np)
                    
                    # Если данные вышли за правую границу, обновляем её
                    if max_time > current_xlim[1]:
                        # Обновляем только правую границу, сохраняя левую
                        new_xlim = [current_xlim[0], max_time]
                        self.ax.set_xlim(new_xlim)
                        self.manual_xlim = new_xlim
                
                # Восстанавливаем ручные границы если они были установлены
                if self.manual_xlim is not None:
                    self.ax.set_xlim(self.manual_xlim)
                if self.manual_ylim is not None:
                    self.ax.set_ylim(self.manual_ylim)
            
            # Обновляем правые метки цен (CEХ/DEX) на текущих значениях
            if len(cex_prices_np) > 0:
                current_cex_val = float(cex_prices_np[-1])
                self.cex_price_label.set_text(f"CEX {current_cex_val:.6f}")
                # Нормализуем позицию по Y в координатах оси
                y0, y1 = self.ax.get_ylim()
                if y1 != y0:
                    cex_rel = (current_cex_val - y0) / (y1 - y0)
                else:
                    cex_rel = 0.5
                self.cex_price_label.set_position((1.01, cex_rel))
                # Обновляем направляющие
                self.cex_guide.set_ydata([current_cex_val, current_cex_val])
                # Бейдж CEX
                self.cex_badge.xy = (self.ax.get_xlim()[1], current_cex_val)
                self.cex_badge.set_text(f"{current_cex_val:.6f}")
                
                if len(dex_prices_np) > 0:
                    current_dex_val = float(dex_prices_np[-1])
                    self.dex_price_label.set_text(f"DEX {current_dex_val:.6f}")
                    if y1 != y0:
                        dex_rel = (current_dex_val - y0) / (y1 - y0)
                    else:
                        dex_rel = 0.5
                    self.dex_price_label.set_position((1.01, dex_rel))
                    self.dex_guide.set_ydata([current_dex_val, current_dex_val])
                    # Бейдж DEX
                    self.dex_badge.xy = (self.ax.get_xlim()[1], current_dex_val)
                    self.dex_badge.set_text(f"{current_dex_val:.6f}")
                else:
                    self.dex_price_label.set_text('')
                    self.dex_guide.set_ydata([np.nan, np.nan])
                    self.dex_badge.set_text('')
            
            # Обновляем спред
            if len(cex_prices_np) > 0 and len(dex_prices_np) > 0:
                current_cex = cex_prices_np[-1]
                current_dex = dex_prices_np[-1]
                if current_cex > 0 and current_dex > 0:
                    spread = ((current_dex - current_cex) / current_cex) * 100
                    self.current_spread = spread  # Сохраняем для ползунка яркости
                    
                    # Цвет спреда в зависимости от значения с учетом яркости
                    brightness = getattr(self, 'spread_brightness', 1.0)
                    if abs(spread) >= 5.0:
                        spread_color = f'#{int(255/brightness):02x}4444'  # Красный для высокого спреда
                    elif abs(spread) >= 2.0:
                        spread_color = f'#{int(255/brightness):02x}aa44'  # Оранжевый для среднего спреда
                    else:
                        spread_color = f'#44{int(255/brightness):02x}44'  # Зеленый для низкого спреда
                    
                    self.spread_text.set_text(f'Current Spread: {spread:+.2f}%')
                    self.spread_text.set_color(spread_color)
            else:
                self.spread_text.set_text('Waiting for data...')
                self.spread_text.set_color('white')
        
        return self.line_cex, self.line_dex
    
    def start(self, token_address, token_symbol, background_monitor=None):
        """Запуск графика"""
        logger.info(f"Starting hybrid chart for {token_symbol}...")
        
        # Обновляем заголовок с названием токена
        self.ax.set_title(f'{token_symbol}/USDT Price Comparison', color='white', fontsize=18, fontweight='bold', pad=20)
        
        # Загружаем историю данных если доступна
        if background_monitor:
            history = background_monitor.get_history(token_symbol)
            if history['times']:
                logger.info(f"Loading {len(history['times'])} historical data points for {token_symbol}")
                # Конвертируем временные метки в datetime объекты
                from datetime import datetime
                self.times = [datetime.fromtimestamp(t) for t in history['times']]
                self.cex_prices = [p for p in history['cex_prices'] if p is not None]
                self.dex_prices = [p for p in history['dex_prices'] if p is not None]
                
                # Синхронизируем данные
                if len(self.cex_prices) > 0:
                    self.mexc_price = self.cex_prices[-1]
                if len(self.dex_prices) > 0:
                    self.dex_price = self.dex_prices[-1]
        
        # Подключаемся к источникам данных
        self.connect_mexc(token_symbol)
        # Пытаемся угадать подсказку сети по символу для EVM: если RAIL/DUSK и т.п., укажите явно в tokens.json (chain)
        chain_hint = None
        if hasattr(self, 'current_chain_hint'):
            chain_hint = self.current_chain_hint
        self.connect_dex(token_address, chain_hint=chain_hint)
        
        # Запускаем анимацию с настраиваемой скоростью
        animation_interval = getattr(self, 'animation_interval', 30)
        self.ani = animation.FuncAnimation(self.fig, self.animate, interval=animation_interval, blit=False, cache_frame_data=False)
        
        logger.info("Hybrid chart started")
        
        # Если это GUI, не показываем plt.show()
        if self.parent_window is None:
            plt.show()
    
    def stop(self):
        """Остановка графика"""
        logger.info("Stopping hybrid chart...")
        self.running = False
        
        # Останавливаем WebSocket соединение
        if self.ws_mexc:
            try:
                self.ws_mexc.close()
                logger.debug("MEXC WebSocket closed")
            except Exception as e:
                logger.error(f"Error closing MEXC WebSocket: {e}")
            finally:
                self.ws_mexc = None
        
        # Останавливаем анимацию
        try:
            if hasattr(self, 'ani') and self.ani:
                self.ani.event_source.stop()
                self.ani = None
                logger.info("Animation stopped")
        except Exception as e:
            logger.error(f"Error stopping animation: {e}")
        
        # Останавливаем все потоки
        try:
            import threading
            for thread in threading.enumerate():
                if thread != threading.current_thread() and hasattr(thread, 'name') and 'chart' in thread.name.lower():
                    logger.info(f"Stopping thread: {thread.name}")
        except Exception as e:
            logger.error(f"Error stopping threads: {e}")
        
        # Очищаем данные
        self.times.clear()
        self.cex_prices.clear()
        self.dex_prices.clear()
        self.mexc_price = None
        self.dex_price = None
        
        logger.info("Hybrid chart stopped")


class BackgroundMonitor:
    """Класс для фонового мониторинга всех токенов и обнаружения спредов"""
    
    def __init__(self, parent_gui):
        self.parent_gui = parent_gui
        self.running = False
        self.monitor_thread = None
        self.tokens_data = []
        self.price_data = {}  # {token_name: {'cex': price, 'dex': price, 'last_update': timestamp}}
        self.spread_threshold = 5.0  # Порог спреда в процентах
        self.monitor_interval = 2.0  # Интервал проверки в секундах
        self.auto_open_charts = True  # Автоматически открывать графики
        self.disable_alerts = False  # Отключить алерты о высоком спреде
        self.opened_charts = set()  # Множество уже открытых графиков для избежания дублирования
        self.blacklisted_tokens = set()  # Черный список токенов
        self.blacklist_file = 'blacklist.json'  # Файл для сохранения черного списка
        
        # WebSocket соединения для каждого токена
        self.ws_connections = {}  # {token_name: websocket}
        self.ws_threads = {}  # {token_name: thread}
        
        # Очередь для передачи данных в GUI поток (ограничиваем размер)
        self.gui_queue = queue.Queue(maxsize=100)
        
        # История данных для всех токенов (15 минут)
        self.history_data = {}  # {token_name: {'times': [], 'cex_prices': [], 'dex_prices': []}}
        self.history_duration = 15 * 60  # 15 минут в секундах
        
        # Загружаем черный список при инициализации
        self.load_blacklist()
        
        logger.info("Background monitor initialized")
    
    def update_history(self, token_name, cex_price=None, dex_price=None):
        """Обновление истории данных для токена"""
        current_time = time.time()
        
        if token_name not in self.history_data:
            self.history_data[token_name] = {
                'times': [],
                'cex_prices': [],
                'dex_prices': []
            }
        
        history = self.history_data[token_name]
        
        # Добавляем новые данные
        if cex_price is not None:
            history['times'].append(current_time)
            history['cex_prices'].append(cex_price)
            # Дублируем последнюю DEX цену если нет новой
            if len(history['dex_prices']) > 0:
                history['dex_prices'].append(history['dex_prices'][-1])
            else:
                history['dex_prices'].append(None)
        
        if dex_price is not None:
            # Если у нас уже есть время для CEX, обновляем DEX цену
            if len(history['times']) > 0:
                history['dex_prices'][-1] = dex_price
            else:
                # Если нет CEX данных, создаем запись только с DEX
                history['times'].append(current_time)
                history['cex_prices'].append(None)
                history['dex_prices'].append(dex_price)
        
        # Очищаем старые данные (старше 15 минут)
        cutoff_time = current_time - self.history_duration
        while history['times'] and history['times'][0] < cutoff_time:
            history['times'].pop(0)
            history['cex_prices'].pop(0)
            history['dex_prices'].pop(0)
    
    def get_history(self, token_name):
        """Получение истории данных для токена"""
        return self.history_data.get(token_name, {
            'times': [],
            'cex_prices': [],
            'dex_prices': []
        })
    
    def load_tokens(self):
        """Загрузка токенов из JSON файла"""
        try:
            if os.path.exists('tokens.json'):
                with open('tokens.json', 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.tokens_data = data.get('tokens', [])
                    logger.info(f"Loaded {len(self.tokens_data)} tokens for monitoring")
            else:
                self.tokens_data = []
                logger.warning("No tokens.json file found")
        except Exception as e:
            logger.error(f"Error loading tokens: {e}")
            self.tokens_data = []
    
    def connect_mexc_websocket(self, token_symbol):
        """Подключение к MEXC Futures WebSocket для конкретного токена"""
        def on_message(ws, message):
            try:
                data = json.loads(message)
                
                # Обработка тикера (раз в ~1s)
                if data.get("channel") == "push.ticker" and "data" in data:
                    ticker_data = data["data"]
                    symbol_name = f"{token_symbol}_USDT"
                    if ticker_data.get("symbol") == symbol_name and "lastPrice" in ticker_data:
                        price = float(ticker_data["lastPrice"])
                        old_price = self.price_data[token_symbol].get('cex')
                        self.price_data[token_symbol]['cex'] = price
                        # Немедленно проверяем спред при изменении цены
                        if old_price != price:
                            self.check_spread_immediately(token_symbol, price, self.price_data[token_symbol].get('dex'))
                        self.price_data[token_symbol]['cex_time'] = time.time()
                        logger.debug(f"MEXC WebSocket Price for {token_symbol}: {price}")
                        
                        # Обновляем историю
                        self.update_history(token_symbol, cex_price=price)
                        
                        # Отправляем сигнал для обновления таблицы (с проверкой переполнения)
                        try:
                            self.gui_queue.put_nowait({
                                'type': 'price_update',
                                'token_name': token_symbol
                            })
                        except queue.Full:
                            logger.debug("GUI queue is full, skipping price update")
                
                # Обработка сделок (tick-by-tick)
                elif data.get("channel") == "push.deal" and "data" in data:
                    deals = data["data"]
                    if isinstance(deals, list) and len(deals) > 0:
                        last = deals[-1]
                        p = last.get("price") or last.get("p")
                        s = last.get("symbol") or last.get("s")
                        symbol_name = f"{token_symbol}_USDT"
                        if p is not None and (s is None or s == symbol_name):
                            try:
                                price = float(p)
                                old_price = self.price_data[token_symbol].get('cex')
                                self.price_data[token_symbol]['cex'] = price
                                self.price_data[token_symbol]['cex_time'] = time.time()
                                
                                # Немедленно проверяем спред при каждой сделке
                                if old_price != price:
                                    dex_price = self.price_data[token_symbol].get('dex')
                                    self.check_spread_immediately(token_symbol, price, dex_price)
                                    logger.debug(f"MEXC Deal Price for {token_symbol}: {price} (change: {price - old_price if old_price else 0})")
                                
                                # Обновляем историю
                                self.update_history(token_symbol, cex_price=price)
                                
                                # Отправляем сигнал для обновления таблицы (с проверкой переполнения)
                                try:
                                    self.gui_queue.put_nowait({
                                        'type': 'price_update',
                                        'token_name': token_symbol
                                    })
                                except queue.Full:
                                    logger.debug("GUI queue is full, skipping price update")
                            except Exception:
                                pass
                
                # Обработка pong ответа
                elif data.get("method") == "pong":
                    pass
                    
            except Exception as e:
                logger.error(f"MEXC message error for {token_symbol}: {e}")
        
        def on_error(ws, error):
            logger.error(f"MEXC WebSocket error for {token_symbol}: {error}")
        
        def on_close(ws, close_status_code, close_msg):
            logger.debug(f"MEXC WebSocket connection closed for {token_symbol}")
            if self.running and token_symbol in self.ws_connections:
                threading.Timer(5.0, lambda: self.connect_mexc_websocket(token_symbol)).start()
        
        def on_open(ws):
            logger.debug(f"MEXC Futures WebSocket connected for {token_symbol}")
            symbol_name = f"{token_symbol}_USDT"
            subscribe_msg = {
                "method": "sub.ticker",
                "param": {
                    "symbol": symbol_name
                }
            }
            ws.send(json.dumps(subscribe_msg))
            logger.info(f"Subscribed to {symbol_name} ticker")
            
            # Дополнительно подписываемся на сделки
            subscribe_deal = {
                "method": "sub.deal",
                "param": {
                    "symbol": symbol_name
                }
            }
            try:
                ws.send(json.dumps(subscribe_deal))
                logger.info(f"Subscribed to {symbol_name} deals")
            except Exception as e:
                logger.error(f"Subscribe deal error for {token_symbol}: {e}")
        
        def ping_loop():
            while self.running and token_symbol in self.ws_connections:
                try:
                    ping_msg = {"method": "ping"}
                    self.ws_connections[token_symbol].send(json.dumps(ping_msg))
                    time.sleep(10)
                except Exception as e:
                    logger.debug(f"MEXC ping error for {token_symbol}: {e}")
                    break
        
        def run_websocket():
            ws = websocket.WebSocketApp(
                "wss://contract.mexc.com/edge",
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
                on_open=on_open
            )
            self.ws_connections[token_symbol] = ws
            threading.Thread(target=ping_loop, daemon=True).start()
            ws.run_forever()
        
        # Запускаем WebSocket в отдельном потоке
        thread = threading.Thread(target=run_websocket, daemon=True)
        thread.start()
        self.ws_threads[token_symbol] = thread
    
    def get_dex_price(self, token_address, chain_hint=None):
        """Получение цены с OKX Web3"""
        try:
            # Определяем блокчейн
            chain = None
            if chain_hint:
                ch = chain_hint.strip().lower()
                mapping = {
                    'ethereum': 'ethereum', 'eth': 'ethereum', 'erc20': 'ethereum',
                    'bsc': 'bsc', 'bep20': 'bsc', 'binance-smart-chain': 'bsc',
                    'sol': 'solana', 'solana': 'solana',
                    'base': 'base',
                    'arbitrum': 'arbitrum', 'arbitrum_one': 'arbitrum', 'arbitrum one': 'arbitrum',
                    'polygon': 'polygon', 'matic': 'polygon'
                }
                chain = mapping.get(ch)
            if chain is None:
                if len(token_address) == 44:
                    chain = 'solana'
                elif token_address.startswith('0x'):
                    chain = 'bsc'
                else:
                    chain = 'bsc'
            
            okx_url = f"https://web3.okx.com/ru/token/{chain}/{token_address}"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            }
            
            response = requests.get(okx_url, headers=headers, timeout=10)
            if response.status_code == 200 and response.text:
                soup = BeautifulSoup(response.content, 'html.parser')
                
                # Ищем цену в различных элементах страницы
                price_selectors = [
                    '.token-price', '.price-value', '[data-testid="token-price"]',
                    '.token-price-value', '.price', '.token-info-price', '.price-display'
                ]
                
                for selector in price_selectors:
                    price_elem = soup.select_one(selector)
                    if price_elem:
                        price_text = price_elem.get_text().strip()
                        price_match = re.search(r'[\d,]+\.?\d*', price_text.replace(',', ''))
                        if price_match:
                            price = float(price_match.group().replace(',', ''))
                            if price > 0:
                                logger.debug(f"OKX Price ({chain.upper()}): {price}")
                                return price
                
                # Поиск в title страницы
                if soup.title:
                    title_text = soup.title.string
                    match = re.search(r'\$([0-9,.]+)', title_text)
                    if match:
                        price = float(match.group(1).replace(',', '.'))
                        return price
                
                # Поиск по regex в тексте страницы
                page_text = response.text
                price_patterns = [
                    r'\$?(\d{1,3}(?:,\d{3})*(?:\.\d+)?)',
                    r'(\d+\.\d+)',
                    r'(\d+,\d+)'
                ]
                
                for pattern in price_patterns:
                    matches = re.findall(pattern, page_text)
                    for match in matches:
                        try:
                            price = float(match.replace(',', ''))
                            if 0.000001 < price < 1000000:
                                return price
                        except ValueError:
                            continue
            return None
        except Exception as e:
            logger.error(f"OKX price fetch error for {token_address}: {e}")
            return None
    
    def check_spread(self, token_name, cex_price, dex_price):
        """Проверка спреда между CEX и DEX ценами"""
        if cex_price is None or dex_price is None or cex_price <= 0 or dex_price <= 0:
            return None
        
        spread = ((dex_price - cex_price) / cex_price) * 100
        return spread
    
    def check_spread_immediately(self, token_name, cex_price, dex_price):
        """Немедленная проверка спреда при изменении цены"""
        if not cex_price or not dex_price:
            return
        
        spread = ((dex_price - cex_price) / cex_price) * 100
        abs_spread = abs(spread)
        
        # Проверяем на алерт (спред больше порога)
        if abs_spread >= self.spread_threshold:
            # Отправляем алерт немедленно
            try:
                if not self.disable_alerts:
                    alert_key = f"{token_name}_{int(abs_spread)}"
                    if alert_key not in self.sent_alerts:
                        message = {
                            'type': 'high_spread',
                            'token': token_name,
                            'spread': spread,
                            'cex_price': cex_price,
                            'dex_price': dex_price,
                            'timestamp': time.time()
                        }
                        self.gui_queue.put(message)
                        self.sent_alerts.add(alert_key)
                        logger.info(f"Immediate high spread alert: {token_name} - {spread:.2f}%")
            except Exception as e:
                logger.error(f"Error in immediate spread check: {e}")
    
    def monitor_loop(self):
        """Основной цикл мониторинга"""
        logger.info("Background monitoring started")
        
        # Подключаемся к WebSocket для всех токенов
        for token in self.tokens_data:
            token_name = token['name']
            self.price_data[token_name] = {}
            self.connect_mexc_websocket(token_name)
            # Убираем задержку - подключаемся максимально быстро
        
        while self.running:
            try:
                current_time = time.time()
                
                for token in self.tokens_data:
                    if not self.running:
                        break
                    
                    token_name = token['name']
                    
                    # Пропускаем токены из черного списка
                    if self.is_blacklisted(token_name):
                        continue
                    
                    token_address = token['address']
                    chain_hint = token.get('chain')
                    
                    # Получаем DEX цену (CEX уже получается через WebSocket)
                    dex_price = self.get_dex_price(token_address, chain_hint)
                    
                    if dex_price is not None:
                        old_dex_price = self.price_data[token_name].get('dex')
                        self.price_data[token_name]['dex'] = dex_price
                        self.price_data[token_name]['dex_time'] = current_time
                        logger.debug(f"Updated DEX price for {token_name}: {dex_price}")
                        
                        # Немедленно проверяем спред при изменении DEX цены
                        if old_dex_price != dex_price:
                            cex_price = self.price_data[token_name].get('cex')
                            self.check_spread_immediately(token_name, cex_price, dex_price)
                        
                        # Обновляем историю
                        self.update_history(token_name, dex_price=dex_price)
                        
                        # Отправляем сигнал для обновления таблицы (с проверкой переполнения)
                        try:
                            self.gui_queue.put_nowait({
                                'type': 'price_update',
                                'token_name': token_name
                            })
                        except queue.Full:
                            logger.debug("GUI queue is full, skipping price update")
                    
                    # Проверяем спред
                    cex_price = self.price_data[token_name].get('cex')
                    if cex_price is not None and dex_price is not None:
                        spread = self.check_spread(token_name, cex_price, dex_price)
                        if spread is not None:
                            logger.debug(f"{token_name}: CEX={cex_price:.6f}, DEX={dex_price:.6f}, Spread={spread:.2f}%")
                            
                            # Проверяем превышение порога
                            if abs(spread) >= self.spread_threshold:
                                chart_key = f"{token_name}_{abs(spread):.1f}"
                                if chart_key not in self.opened_charts and self.auto_open_charts:
                                    logger.warning(f"High spread detected for {token_name}: {spread:.2f}%")
                                    
                                    # Отправляем сигнал в GUI для открытия графика только если алерты не отключены
                                    if not self.disable_alerts:
                                        try:
                                            self.gui_queue.put_nowait({
                                                'type': 'high_spread',
                                                'token': token,
                                                'spread': spread,
                                                'cex_price': cex_price,
                                                'dex_price': dex_price
                                            })
                                        except queue.Full:
                                            logger.debug("GUI queue is full, skipping high spread alert")
                                    
                                    self.opened_charts.add(chart_key)
                                    
                                    # Удаляем из множества через 5 минут
                                    threading.Timer(300, lambda: self.opened_charts.discard(chart_key)).start()
                
                # Убираем sleep - работаем в реальном времени
                # time.sleep(0.1)  # Убрано для максимальной скорости
                
            except Exception as e:
                logger.error(f"Error in monitor loop: {e}")
                time.sleep(5)  # Пауза при ошибке
        
        logger.info("Background monitoring stopped")
    
    def start_monitoring(self):
        """Запуск мониторинга"""
        if self.running:
            logger.warning("Monitoring is already running")
            return
        
        self.load_tokens()
        if not self.tokens_data:
            logger.warning("No tokens to monitor")
            return
        
        self.running = True
        self.monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
        self.monitor_thread.start()
        logger.info(f"Started monitoring {len(self.tokens_data)} tokens")
    
    def stop_monitoring(self):
        """Остановка мониторинга"""
        if not self.running:
            return
        
        logger.info("Stopping background monitoring...")
        self.running = False
        
        # Закрываем все WebSocket соединения
        for token_name, ws in list(self.ws_connections.items()):
            try:
                if ws:
                    ws.close()
                    logger.debug(f"Closed WebSocket for {token_name}")
            except Exception as e:
                logger.error(f"Error closing WebSocket for {token_name}: {e}")
        
        # Очищаем соединения
        self.ws_connections.clear()
        self.ws_threads.clear()
        
        # Останавливаем основной поток мониторинга
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=3)
            if self.monitor_thread.is_alive():
                logger.warning("Monitor thread did not stop gracefully")
        
        # Очищаем все данные
        self.price_data.clear()
        self.history_data.clear()
        
        logger.info("Background monitoring stopped")
    
    def update_settings(self, spread_threshold=None, monitor_interval=None, auto_open_charts=None, disable_alerts=None):
        """Обновление настроек мониторинга"""
        if spread_threshold is not None:
            self.spread_threshold = spread_threshold
        if monitor_interval is not None:
            self.monitor_interval = monitor_interval
        if auto_open_charts is not None:
            self.auto_open_charts = auto_open_charts
        if disable_alerts is not None:
            self.disable_alerts = disable_alerts
        logger.info(f"Monitor settings updated: threshold={self.spread_threshold}%, interval={self.monitor_interval}s, auto_open={self.auto_open_charts}, disable_alerts={self.disable_alerts}")
    
    def add_to_blacklist(self, token_name):
        """Добавить токен в черный список"""
        self.blacklisted_tokens.add(token_name)
        self.save_blacklist()  # Сохраняем изменения
        logger.info(f"Token {token_name} added to blacklist")
    
    def remove_from_blacklist(self, token_name):
        """Удалить токен из черного списка"""
        self.blacklisted_tokens.discard(token_name)
        self.save_blacklist()  # Сохраняем изменения
        logger.info(f"Token {token_name} removed from blacklist")
    
    def is_blacklisted(self, token_name):
        """Проверить, находится ли токен в черном списке"""
        return token_name in self.blacklisted_tokens
    
    def get_blacklisted_tokens(self):
        """Получить список токенов в черном списке"""
        return list(self.blacklisted_tokens)
    
    def load_blacklist(self):
        """Загрузка черного списка из файла"""
        try:
            if os.path.exists(self.blacklist_file):
                with open(self.blacklist_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.blacklisted_tokens = set(data.get('blacklisted_tokens', []))
                    logger.info(f"Loaded blacklist with {len(self.blacklisted_tokens)} tokens")
            else:
                self.blacklisted_tokens = set()
                logger.info("No blacklist file found, starting with empty blacklist")
        except Exception as e:
            logger.error(f"Error loading blacklist: {e}")
            self.blacklisted_tokens = set()
    
    def save_blacklist(self):
        """Сохранение черного списка в файл"""
        try:
            data = {
                'blacklisted_tokens': list(self.blacklisted_tokens),
                'last_updated': time.time()
            }
            with open(self.blacklist_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info(f"Blacklist saved with {len(self.blacklisted_tokens)} tokens")
        except Exception as e:
            logger.error(f"Error saving blacklist: {e}")


class TokenDialog:
    def __init__(self, parent):
        self.result = None
        
        # Создаем диалоговое окно
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Add New Token")
        self.dialog.geometry("400x300")
        self.dialog.configure(bg='#2b2b2b')
        self.dialog.transient(parent)
        self.dialog.grab_set()
        
        # Центрируем окно
        self.dialog.geometry("+%d+%d" % (parent.winfo_rootx() + 50, parent.winfo_rooty() + 50))
        
        self.setup_ui()
    
    def setup_ui(self):
        main_frame = ttk.Frame(self.dialog, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Заголовок
        ttk.Label(main_frame, text="Add New Token", 
                 font=('Arial', 14, 'bold')).pack(pady=(0, 20))
        
        # Поля ввода
        ttk.Label(main_frame, text="Token Name:", 
                 font=('Arial', 10, 'bold')).pack(anchor=tk.W, pady=(0, 5))
        self.name_entry = ttk.Entry(main_frame, width=40, font=('Arial', 10))
        self.name_entry.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(main_frame, text="Token Address:", 
                 font=('Arial', 10, 'bold')).pack(anchor=tk.W, pady=(0, 5))
        self.address_entry = ttk.Entry(main_frame, width=40, font=('Arial', 10))
        self.address_entry.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(main_frame, text="Blockchain:", 
                 font=('Arial', 10, 'bold')).pack(anchor=tk.W, pady=(0, 5))
        self.chain_combobox = ttk.Combobox(main_frame, width=37, font=('Arial', 10))
        self.chain_combobox['values'] = ['BSC', 'Ethereum', 'Solana', 'Polygon', 'Arbitrum', 'Optimism']
        self.chain_combobox.set('BSC')
        self.chain_combobox.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(main_frame, text="Description (optional):", 
                 font=('Arial', 10, 'bold')).pack(anchor=tk.W, pady=(0, 5))
        self.description_entry = ttk.Entry(main_frame, width=40, font=('Arial', 10))
        self.description_entry.pack(fill=tk.X, pady=(0, 20))
        
        # Кнопки
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X)
        
        ttk.Button(button_frame, text="Cancel", 
                  command=self.cancel).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(button_frame, text="Add Token", 
                  command=self.add_token).pack(side=tk.RIGHT)
        
        # Фокус на первое поле
        self.name_entry.focus()
        
        # Привязка Enter
        self.name_entry.bind('<Return>', lambda e: self.address_entry.focus())
        self.address_entry.bind('<Return>', lambda e: self.chain_combobox.focus())
        self.chain_combobox.bind('<Return>', lambda e: self.description_entry.focus())
        self.description_entry.bind('<Return>', lambda e: self.add_token())
    
    def add_token(self):
        """Добавление токена"""
        name = self.name_entry.get().strip().upper()
        address = self.address_entry.get().strip()
        chain = self.chain_combobox.get().strip()
        description = self.description_entry.get().strip()
        
        if not name or not address or not chain:
            messagebox.showerror("Error", "Please fill in all required fields")
            return
        
        self.result = {
            'name': name,
            'address': address,
            'chain': chain,
            'description': description
        }
        
        self.dialog.destroy()
    
    def cancel(self):
        """Отмена"""
        self.dialog.destroy()


class BlacklistDialog:
    def __init__(self, parent, monitor, gui):
        self.monitor = monitor
        self.gui = gui
        self.result = None
        
        # Создаем диалоговое окно
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Token Blacklist Management")
        self.dialog.geometry("800x700")
        self.dialog.configure(bg='#2b2b2b')
        self.dialog.transient(parent)
        self.dialog.grab_set()
        
        # Центрируем окно
        self.dialog.geometry("+%d+%d" % (parent.winfo_rootx() + 50, parent.winfo_rooty() + 50))
        
        self.setup_ui()
    
    def setup_ui(self):
        main_frame = ttk.Frame(self.dialog, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Заголовок
        ttk.Label(main_frame, text="Token Blacklist Management", 
                 font=('Arial', 14, 'bold')).pack(pady=(0, 20))
        
        # Информация
        info_label = ttk.Label(main_frame, text="Blacklisted tokens will be excluded from monitoring and spread table", 
                              font=('Arial', 9), foreground='gray')
        info_label.pack(anchor=tk.W, pady=(0, 10))
        
        # Список всех токенов
        ttk.Label(main_frame, text="Available Tokens:", 
                 font=('Arial', 10, 'bold')).pack(anchor=tk.W, pady=(10, 5))
        
        # Фрейм для списков
        lists_frame = ttk.Frame(main_frame)
        lists_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        
        # Левая колонка - доступные токены
        left_frame = ttk.LabelFrame(lists_frame, text="Available Tokens", padding="10")
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        
        # Список доступных токенов
        self.available_listbox = tk.Listbox(left_frame, height=12, font=('Arial', 9))
        self.available_listbox.pack(fill=tk.BOTH, expand=True)
        
        # Скроллбар для левого списка
        left_scrollbar = ttk.Scrollbar(left_frame, orient=tk.VERTICAL, command=self.available_listbox.yview)
        self.available_listbox.configure(yscrollcommand=left_scrollbar.set)
        left_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Правая колонка - черный список
        right_frame = ttk.LabelFrame(lists_frame, text="Blacklisted Tokens", padding="10")
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        
        # Список токенов в черном списке
        self.blacklisted_listbox = tk.Listbox(right_frame, height=12, font=('Arial', 9))
        self.blacklisted_listbox.pack(fill=tk.BOTH, expand=True)
        
        # Скроллбар для правого списка
        right_scrollbar = ttk.Scrollbar(right_frame, orient=tk.VERTICAL, command=self.blacklisted_listbox.yview)
        self.blacklisted_listbox.configure(yscrollcommand=right_scrollbar.set)
        right_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Кнопки управления
        buttons_frame = ttk.Frame(main_frame)
        buttons_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(buttons_frame, text="➡ Add to Blacklist", 
                  command=self.add_to_blacklist).pack(side=tk.LEFT, padx=5)
        ttk.Button(buttons_frame, text="⬅ Remove from Blacklist", 
                  command=self.remove_from_blacklist).pack(side=tk.LEFT, padx=5)
        ttk.Button(buttons_frame, text="🔄 Refresh Lists", 
                  command=self.refresh_lists).pack(side=tk.LEFT, padx=5)
        
        # Кнопки закрытия
        close_frame = ttk.Frame(main_frame)
        close_frame.pack(fill=tk.X, pady=(20, 0))
        
        ttk.Button(close_frame, text="Close", 
                  command=self.dialog.destroy).pack(side=tk.RIGHT)
        
        # Заполняем списки
        self.refresh_lists()
    
    def refresh_lists(self):
        """Обновление списков токенов"""
        # Очищаем списки
        self.available_listbox.delete(0, tk.END)
        self.blacklisted_listbox.delete(0, tk.END)
        
        # Получаем черный список
        blacklisted = set(self.monitor.get_blacklisted_tokens())
        
        # Заполняем доступные токены
        for token in self.gui.tokens_data:
            token_name = token['name']
            if token_name not in blacklisted:
                display_text = f"{token_name} ({token['chain']})"
                self.available_listbox.insert(tk.END, display_text)
        
        # Заполняем черный список
        for token_name in blacklisted:
            # Находим информацию о токене
            token_info = None
            for token in self.gui.tokens_data:
                if token['name'] == token_name:
                    token_info = token
                    break
            
            if token_info:
                display_text = f"{token_name} ({token_info['chain']})"
            else:
                display_text = token_name
            
            self.blacklisted_listbox.insert(tk.END, display_text)
    
    def add_to_blacklist(self):
        """Добавление токена в черный список"""
        selection = self.available_listbox.curselection()
        if not selection:
            messagebox.showwarning("Warning", "Please select a token to add to blacklist")
            return
        
        # Получаем выбранный токен
        selected_text = self.available_listbox.get(selection[0])
        token_name = selected_text.split(' (')[0]  # Извлекаем имя токена
        
        # Добавляем в черный список
        self.monitor.add_to_blacklist(token_name)
        
        # Обновляем списки
        self.refresh_lists()
        
        # Обновляем таблицу спредов
        if hasattr(self.gui, 'spread_tree'):
            self.gui.update_spread_table()
        
        messagebox.showinfo("Success", f"Token {token_name} added to blacklist")
    
    def remove_from_blacklist(self):
        """Удаление токена из черного списка"""
        selection = self.blacklisted_listbox.curselection()
        if not selection:
            messagebox.showwarning("Warning", "Please select a token to remove from blacklist")
            return
        
        # Получаем выбранный токен
        selected_text = self.blacklisted_listbox.get(selection[0])
        token_name = selected_text.split(' (')[0]  # Извлекаем имя токена
        
        # Удаляем из черного списка
        self.monitor.remove_from_blacklist(token_name)
        
        # Обновляем списки
        self.refresh_lists()
        
        # Обновляем таблицу спредов
        if hasattr(self.gui, 'spread_tree'):
            self.gui.update_spread_table()
        
        messagebox.showinfo("Success", f"Token {token_name} removed from blacklist")


class MonitorSettingsDialog:
    def __init__(self, parent, monitor):
        self.monitor = monitor
        self.result = None
        
        # Создаем диалоговое окно
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Monitor Settings")
        self.dialog.geometry("400x300")
        self.dialog.configure(bg='#2b2b2b')
        self.dialog.transient(parent)
        self.dialog.grab_set()
        
        # Центрируем окно
        self.dialog.geometry("+%d+%d" % (parent.winfo_rootx() + 50, parent.winfo_rooty() + 50))
        
        self.setup_ui()
    
    def setup_ui(self):
        main_frame = ttk.Frame(self.dialog, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Заголовок
        ttk.Label(main_frame, text="Background Monitor Settings", 
                 font=('Arial', 14, 'bold')).pack(pady=(0, 20))
        
        # Настройки
        ttk.Label(main_frame, text="Spread Threshold (%):", 
                 font=('Arial', 10, 'bold')).pack(anchor=tk.W, pady=(0, 5))
        self.threshold_var = tk.StringVar(value=str(self.monitor.spread_threshold))
        self.threshold_entry = ttk.Entry(main_frame, textvariable=self.threshold_var, width=40, font=('Arial', 10))
        self.threshold_entry.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(main_frame, text="Monitor Interval (seconds):", 
                 font=('Arial', 10, 'bold')).pack(anchor=tk.W, pady=(0, 5))
        self.interval_var = tk.StringVar(value=str(self.monitor.monitor_interval))
        self.interval_entry = ttk.Entry(main_frame, textvariable=self.interval_var, width=40, font=('Arial', 10))
        self.interval_entry.pack(fill=tk.X, pady=(0, 10))
        
        # Чекбокс для автоматического открытия графиков
        self.auto_open_var = tk.BooleanVar(value=self.monitor.auto_open_charts)
        auto_open_check = ttk.Checkbutton(main_frame, text="Auto-open charts on high spread", 
                                        variable=self.auto_open_var)
        auto_open_check.pack(anchor=tk.W, pady=(0, 10))
        
        # Чекбокс для отключения алертов
        self.disable_alerts_var = tk.BooleanVar(value=getattr(self.monitor, 'disable_alerts', False))
        disable_alerts_check = ttk.Checkbutton(main_frame, text="Disable high spread alerts", 
                                             variable=self.disable_alerts_var)
        disable_alerts_check.pack(anchor=tk.W, pady=(0, 20))
        
        # Кнопки
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X)
        
        ttk.Button(button_frame, text="Cancel", 
                  command=self.cancel).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(button_frame, text="Save Settings", 
                  command=self.save_settings).pack(side=tk.RIGHT)
        
        # Фокус на первое поле
        self.threshold_entry.focus()
    
    def save_settings(self):
        """Сохранение настроек"""
        try:
            threshold = float(self.threshold_var.get())
            interval = float(self.interval_var.get())
            auto_open = self.auto_open_var.get()
            disable_alerts = self.disable_alerts_var.get()
            
            if threshold <= 0 or interval <= 0:
                messagebox.showerror("Error", "Values must be positive numbers")
                return
            
            self.monitor.update_settings(threshold, interval, auto_open, disable_alerts)
            self.dialog.destroy()
            
        except ValueError:
            messagebox.showerror("Error", "Please enter valid numbers")
    
    def cancel(self):
        """Отмена"""
        self.dialog.destroy()


class ChartGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Hybrid Price Chart (MEXC Futures + OKX)")
        self.root.geometry("1000x700")
        self.root.configure(bg='#000000')
        # Тёмная шапка окна (Windows)
        enable_dark_title_bar(self.root)
        
        # Список активных графиков
        self.charts = []
        self._double_click_handled = False  # Флаг для обработки двойного клика
        self.open_chart_count = 0  # Счетчик открытых окон графиков
        self.charts_always_on_top = True  # Флаг для окон поверх всех
        
        # Загружаем токены из JSON
        self.tokens_data = self.load_tokens()
        
        # Инициализируем фоновый мониторинг
        self.background_monitor = BackgroundMonitor(self)
        
        # Отслеживание отправленных алертов для предотвращения спама
        self.sent_alerts = set()  # Множество уже отправленных алертов
        
        # Обработчик закрытия окна
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # Применяем тёмную тему для ttk
        self.setup_theme()
        
        self.setup_ui()
        
        # Запускаем фоновый мониторинг автоматически
        self.background_monitor.start_monitoring()
        logger.info("Background monitoring started automatically on startup")
        
        # Запускаем обработку очереди мониторинга
        self.last_table_update = 0
        self.process_monitor_queue()
    
    def load_tokens(self):
        """Загрузка токенов из JSON файла"""
        try:
            if os.path.exists('tokens.json'):
                with open('tokens.json', 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return data.get('tokens', [])
            else:
                # Создаем файл по умолчанию
                default_tokens = {
                    "tokens": [
                        {
                            "name": "BNBHOLDER",
                            "address": "0x44440f83419de123d7d411187adb9962db017d03",
                            "chain": "BSC",
                            "description": "BNB Holder Token"
                        },
                        {
                            "name": "STREAMER",
                            "address": "3arUrpH3nzaRJbbpVgY42dcqSq9A5BFgUxKozZ4npump",
                            "chain": "Solana",
                            "description": "Streamer Token"
                        }
                    ]
                }
                with open('tokens.json', 'w', encoding='utf-8') as f:
                    json.dump(default_tokens, f, indent=2, ensure_ascii=False)
                return default_tokens['tokens']
        except Exception as e:
            logger.error(f"Error loading tokens: {e}")
            return []
    
    def save_tokens(self):
        """Сохранение токенов в JSON файл"""
        try:
            data = {"tokens": self.tokens_data}
            with open('tokens.json', 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving tokens: {e}")
            messagebox.showerror("Error", f"Failed to save tokens: {str(e)}")

    def setup_theme(self):
        """Настроить тёмную тему для ttk-виджетов"""
        try:
            style = ttk.Style(self.root)
            # Надёжная база для кастомизации
            try:
                style.theme_use('clam')
            except Exception:
                pass
            bg = '#000000'
            fg = '#ffffff'
            accent = '#0f0f0f'
            hover = '#1a1a1a'
            self.root.tk_setPalette(background=bg, foreground=fg, activeBackground=hover, activeForeground=fg)
            # Общие контейнеры
            style.configure('TFrame', background=bg)
            style.configure('TLabelframe', background=bg, foreground=fg)
            style.configure('TLabelframe.Label', background=bg, foreground=fg)
            # Тексты и подписи
            style.configure('TLabel', background=bg, foreground=fg)
            # Кнопки
            style.configure('TButton', background=accent, foreground=fg)
            style.map('TButton', background=[('active', hover)])
            # Комбобокс
            style.configure('TCombobox', fieldbackground=bg, background=accent, foreground=fg)
            style.map('TCombobox', fieldbackground=[('readonly', bg)], foreground=[('readonly', fg)], background=[('readonly', accent)])
            # Поля ввода (если будут)
            style.configure('TEntry', fieldbackground=bg, foreground=fg, background=accent)
        except Exception as e:
            logger.error(f"Failed to apply dark theme: {e}")
    
    def process_monitor_queue(self):
        """Обработка очереди сообщений от фонового мониторинга"""
        try:
            # Обрабатываем только одно сообщение за раз, чтобы не блокировать GUI
            message = self.background_monitor.gui_queue.get_nowait()
            
            if message['type'] == 'high_spread':
                self.handle_high_spread_alert(message)
            elif message['type'] == 'price_update':
                # Обновляем таблицу с ограничением частоты (не чаще раза в секунду)
                current_time = time.time()
                if hasattr(self, 'spread_tree') and current_time - self.last_table_update > 1.0:
                    self.refresh_spread_table()
                    self.last_table_update = current_time
                
        except queue.Empty:
            pass
        
        # Планируем следующую проверку с разумной задержкой
        self.root.after(500, self.process_monitor_queue)
    
    def handle_high_spread_alert(self, message):
        """Обработка уведомления о высоком спреде"""
        token = message['token']
        spread = message['spread']
        cex_price = message['cex_price']
        dex_price = message['dex_price']
        
        logger.info(f"High spread alert: {token['name']} - {spread:.2f}%")
        
        # Показываем уведомление
        alert_msg = f"High spread detected!\n\nToken: {token['name']}\nSpread: {spread:.2f}%\nCEX Price: {cex_price:.6f}\nDEX Price: {dex_price:.6f}\n\nOpen chart?"
        
        if messagebox.askyesno("High Spread Alert", alert_msg):
            # Копируем тикер в буфер обмена
            ticker = f"{token['name']}USDT"
            try:
                pyperclip.copy(ticker)
                logger.info(f"Copied ticker to clipboard: {ticker}")
            except Exception as e:
                logger.error(f"Failed to copy to clipboard: {e}")
            
            # Автоматически открываем график
            self.open_chart_for_token(token)
    
    def open_chart_for_token(self, token):
        """Открытие графика для конкретного токена"""
        click_logger.info(f"=== OPENING CHART FOR TOKEN ===")
        click_logger.info(f"Token data: {token}")
        try:
            address = token['address']
            symbol = token['name']
            chain_hint = token.get('chain')
            
            click_logger.info(f"Opening chart for {symbol} (address: {address}, chain: {chain_hint})")
            
            # Создаем новое окно для графика
            chart_window = tk.Toplevel(self.root)
            chart_window.title(f"{symbol} Price Chart (Auto-opened)")
            chart_window.geometry("500x350")
            chart_window.configure(bg='#2b2b2b')
            
            # Позиционируем окно в правом верхнем углу экрана с каскадным расположением
            screen_width = chart_window.winfo_screenwidth()
            screen_height = chart_window.winfo_screenheight()
            window_width = 500
            window_height = 350
            
            # Вычисляем позицию для каскадного расположения
            x_position = screen_width - window_width - 20  # 20px отступ от края
            y_position = 20 + (self.open_chart_count * 370)  # Каждое окно на 370px ниже предыдущего
            
            chart_window.geometry(f"{window_width}x{window_height}+{x_position}+{y_position}")
            
            # Увеличиваем счетчик открытых окон
            self.open_chart_count += 1
            
            # Тёмная шапка окна (Windows)
            enable_dark_title_bar(chart_window)
            
            # Применяем настройку "поверх всех окон" если она включена
            if self.charts_always_on_top:
                chart_window.attributes('-topmost', True)
            
            # Создаем график
            chart = HybridChart(chart_window)
            chart.current_symbol = symbol
            
            # Встраиваем matplotlib в tkinter
            canvas = FigureCanvasTkAgg(chart.fig, chart_window)
            canvas.draw()
            canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
            
            # Панель управления
            control_frame = ttk.Frame(chart_window)
            control_frame.pack(fill=tk.X, padx=10, pady=5)
            
            stop_button = ttk.Button(control_frame, text="Stop Chart", 
                                   command=lambda: self.stop_chart(chart, chart_window))
            stop_button.pack(side=tk.LEFT)
            
            # Кнопки управления масштабом
            zoom_frame = ttk.Frame(control_frame)
            zoom_frame.pack(side=tk.LEFT, padx=(20, 0))
            
            ttk.Button(zoom_frame, text="🔍+", 
                      command=lambda: self.zoom_in(chart)).pack(side=tk.LEFT, padx=2)
            ttk.Button(zoom_frame, text="🔍-", 
                      command=lambda: self.zoom_out(chart)).pack(side=tk.LEFT, padx=2)
            ttk.Button(zoom_frame, text="🏠 Reset", 
                      command=lambda: self.reset_zoom(chart)).pack(side=tk.LEFT, padx=2)
            
            # Подсказка
            help_label = ttk.Label(control_frame, text="💡 Mouse wheel: zoom | Left drag: pan", 
                                 font=('Arial', 8), foreground='gray')
            help_label.pack(side=tk.LEFT, padx=(20, 0))
            
            status_label = ttk.Label(control_frame, text=f"Auto-opened chart for {symbol} (High spread detected)")
            status_label.pack(side=tk.LEFT, padx=20)
            
            # Панель ползунков
            sliders_frame = ttk.LabelFrame(chart_window, text="Chart Controls", padding="10")
            sliders_frame.pack(fill=tk.X, padx=10, pady=5)
            
            # Создаем ползунки
            self.create_chart_sliders(sliders_frame, chart)
            
            # Обработчик закрытия окна графика
            chart_window.protocol("WM_DELETE_WINDOW", lambda: self.close_chart_window(chart, chart_window))
            
            # Запускаем график
            chart.current_chain_hint = chain_hint
            chart.start(address, symbol, self.background_monitor)
            
            # Добавляем в список активных графиков
            self.charts.append((chart, chart_window))
            
            click_logger.info(f"Successfully opened chart for {symbol}")
            
        except Exception as e:
            click_logger.error(f"Error opening chart for {token['name']}: {e}")
            click_logger.error(f"Exception details: {str(e)}")
            messagebox.showerror("Error", f"Failed to open chart: {str(e)}")
    
    def close_chart_window(self, chart, chart_window):
        """Закрытие окна графика с уменьшением счетчика"""
        # Уменьшаем счетчик открытых окон
        if self.open_chart_count > 0:
            self.open_chart_count -= 1
        
        # Останавливаем график и закрываем окно
        self.stop_chart(chart, chart_window)
    
    def toggle_charts_always_on_top(self):
        """Переключение режима 'поверх всех окон' для всех графиков"""
        self.charts_always_on_top = not self.charts_always_on_top
        
        # Применяем настройку ко всем открытым графикам
        for chart, chart_window in self.charts:
            try:
                chart_window.attributes('-topmost', self.charts_always_on_top)
            except Exception as e:
                logger.error(f"Error setting topmost attribute: {e}")
        
        # Обновляем текст кнопки
        if hasattr(self, 'always_on_top_button'):
            if self.charts_always_on_top:
                self.always_on_top_button.config(text="📌 Charts: Always On Top ✓")
            else:
                self.always_on_top_button.config(text="📌 Charts: Always On Top")
        
        logger.info(f"Charts always on top: {self.charts_always_on_top}")
    
    def start_background_monitoring(self):
        """Запуск фонового мониторинга"""
        if self.background_monitor.running:
            messagebox.showinfo("Info", "Background monitoring is already running")
            return
        
        self.background_monitor.start_monitoring()
        self.start_monitor_button.config(text="Start Background Monitor ✓")
        self.status_label.config(text="Background monitoring started")
        logger.info("Background monitoring started from GUI")
    
    def stop_background_monitoring(self):
        """Остановка фонового мониторинга"""
        if not self.background_monitor.running:
            messagebox.showinfo("Info", "Background monitoring is not running")
            return
        
        self.background_monitor.stop_monitoring()
        self.start_monitor_button.config(text="Start Background Monitor")
        self.status_label.config(text="Background monitoring stopped")
        logger.info("Background monitoring stopped from GUI")
    
    def open_monitor_settings(self):
        """Открытие настроек мониторинга"""
        MonitorSettingsDialog(self.root, self.background_monitor)
    
    def toggle_alerts(self):
        """Переключение состояния алертов"""
        self.background_monitor.disable_alerts = not self.background_monitor.disable_alerts
        
        if self.background_monitor.disable_alerts:
            self.toggle_alerts_button.config(text="🔔 Enable Alerts")
            self.status_label.config(text="High spread alerts disabled")
            logger.info("High spread alerts disabled")
        else:
            self.toggle_alerts_button.config(text="🔕 Disable Alerts")
            self.status_label.config(text="High spread alerts enabled")
            logger.info("High spread alerts enabled")
    
    def open_blacklist_dialog(self):
        """Открытие диалога управления черным списком"""
        BlacklistDialog(self.root, self.background_monitor, self)
    
    
    def update_spread_table(self):
        """Обновление таблицы спредов"""
        # Сохраняем текущее выделение
        selected_items = self.spread_tree.selection()
        selected_token = None
        if selected_items:
            values = self.spread_tree.item(selected_items[0], 'values')
            if values:
                selected_token = values[0]  # Сохраняем имя токена
        
        # Очищаем таблицу
        for item in self.spread_tree.get_children():
            self.spread_tree.delete(item)
        
        # Собираем данные всех токенов с вычисленными спредами
        token_data_list = []
        
        for token in self.tokens_data:
            token_name = token['name']
            
            # Пропускаем токены из черного списка
            if self.background_monitor.is_blacklisted(token_name):
                continue
                
            chain = token['chain']
            
            # Получаем данные о ценах из мониторинга
            price_data = self.background_monitor.price_data.get(token_name, {})
            cex_price = price_data.get('cex')
            dex_price = price_data.get('dex')
            
            # Форматируем цены
            cex_str = f"{cex_price:.6f}" if cex_price else "N/A"
            dex_str = f"{dex_price:.6f}" if dex_price else "N/A"
            
            logger.debug(f"Token {token_name}: CEX={cex_price}, DEX={dex_price}")
            
            # Вычисляем спред
            if cex_price and dex_price and cex_price > 0:
                spread = ((dex_price - cex_price) / cex_price) * 100
                spread_str = f"{spread:.2f}%"
                abs_spread = abs(spread)
                
                # Проверяем на алерт (спред больше порога или меньше -порога)
                if abs_spread >= self.background_monitor.spread_threshold:
                    # Отправляем алерт
                    self.send_spread_alert(token, spread, cex_price, dex_price)
                
                # Определяем статус
                if abs_spread >= self.background_monitor.spread_threshold:
                    status = "HIGH SPREAD!"
                    tags = ('high_spread',)
                elif abs_spread >= 2.0:
                    status = "Medium"
                    tags = ('medium_spread',)
                else:
                    status = "Low"
                    tags = ('low_spread',)
            else:
                spread_str = "N/A"
                abs_spread = 0  # Для сортировки
                status = "No data"
                tags = ('no_data',)
            
            # Добавляем данные в список для сортировки
            token_data_list.append({
                'token_name': token_name,
                'chain': chain,
                'cex_str': cex_str,
                'dex_str': dex_str,
                'spread_str': spread_str,
                'status': status,
                'tags': tags,
                'abs_spread': abs_spread
            })
        
        # Сортируем по абсолютному значению спреда (от большего к меньшему)
        token_data_list.sort(key=lambda x: x['abs_spread'], reverse=True)
        
        # Добавляем отсортированные данные в таблицу
        click_logger.info(f"=== UPDATING SPREAD TABLE ===")
        click_logger.info(f"Total tokens to add: {len(token_data_list)}")
        for token_data in token_data_list:
            click_logger.info(f"Adding token: {token_data['token_name']} with tags: {token_data['tags']} (spread: {token_data['spread_str']})")
            item_id = self.spread_tree.insert('', 'end', values=(
                token_data['token_name'],
                token_data['chain'],
                token_data['cex_str'],
                token_data['dex_str'],
                token_data['spread_str'],
                token_data['status']
            ), tags=token_data['tags'])
            
            # Восстанавливаем выделение для выбранного токена
            if selected_token and token_data['token_name'] == selected_token:
                self.spread_tree.selection_set(item_id)
        
        # Настраиваем цвета для разных статусов
        self.spread_tree.tag_configure('high_spread', background='#ff4444', foreground='white')
        self.spread_tree.tag_configure('medium_spread', background='#ffaa44', foreground='black')
        self.spread_tree.tag_configure('low_spread', background='#44ff44', foreground='black')
        self.spread_tree.tag_configure('no_data', background='#666666', foreground='white')
    
    def send_spread_alert(self, token, spread, cex_price, dex_price):
        """Отправка алерта о высоком спреде из таблицы"""
        # Проверяем, не отключены ли алерты
        if self.background_monitor.disable_alerts:
            return
        
        # Создаем уникальный ключ для алерта
        alert_key = f"{token['name']}_{abs(spread):.1f}"
        
        # Проверяем, не был ли уже отправлен алерт для этого токена с таким спредом
        if alert_key in self.sent_alerts:
            return
        
        # Добавляем в множество отправленных алертов
        self.sent_alerts.add(alert_key)
        
        # Очищаем старые алерты через 5 минут
        import threading
        threading.Timer(300, lambda: self.sent_alerts.discard(alert_key)).start()
        
        # Звуковой сигнал
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except ImportError:
            try:
                import os
                os.system('echo \a')
            except:
                pass
        
        # Показываем уведомление
        alert_msg = f"🚨 HIGH SPREAD ALERT! 🚨\n\nToken: {token['name']}\nSpread: {spread:.2f}%\nCEX Price: {cex_price:.6f}\nDEX Price: {dex_price:.6f}\n\nOpen chart?"
        
        # Создаем временное окно для настройки messagebox
        temp_window = tk.Toplevel(self.root)
        temp_window.withdraw()  # Скрываем окно
        temp_window.attributes('-topmost', True)  # Устанавливаем поверх всех окон
        
        # Показываем messagebox
        result = messagebox.askyesno("🚨 High Spread Alert", alert_msg, parent=temp_window)
        
        # Закрываем временное окно
        temp_window.destroy()
        
        if result:
            # Копируем тикер в буфер обмена
            ticker = f"{token['name']}USDT"
            try:
                pyperclip.copy(ticker)
                logger.info(f"Copied ticker to clipboard: {ticker}")
            except Exception as e:
                logger.error(f"Failed to copy to clipboard: {e}")
            
            # Автоматически открываем график
            self.open_chart_for_token(token)
    
    def refresh_spread_table(self):
        """Обновление таблицы спредов (вызывается периодически)"""
        if hasattr(self, 'spread_tree'):
            self.update_spread_table()
    
    def on_spread_table_single_click(self, event):
        """Обработка одинарного клика по таблице спредов"""
        click_logger.info("=== SINGLE CLICK EVENT ===")
        # Получаем элемент под курсором
        item = self.spread_tree.identify_row(event.y)
        click_logger.info(f"Clicked item: {item}")
        if item:
            # Выделяем элемент
            self.spread_tree.selection_set(item)
            
            # Получаем данные токена
            values = self.spread_tree.item(item, 'values')
            click_logger.info(f"Item values: {values}")
            if values:
                token_name = values[0]
                click_logger.info(f"Single click on token: {token_name}")
                
                # Получаем теги элемента
                tags = self.spread_tree.item(item, 'tags')
                click_logger.info(f"Item tags: {tags}")
                
                # Копируем тикер в буфер обмена (например, RAILUSDT)
                ticker = f"{token_name}USDT"
                try:
                    pyperclip.copy(ticker)
                    click_logger.info(f"Copied ticker to clipboard: {ticker}")
                except Exception as e:
                    click_logger.error(f"Failed to copy to clipboard: {e}")
                
                # Запускаем открытие графика с небольшой задержкой, чтобы избежать конфликта с двойным кликом
                click_logger.info(f"Scheduling delayed chart open for {token_name}")
                self.root.after(200, lambda: self._delayed_open_chart(token_name))
    
    def _delayed_open_chart(self, token_name):
        """Отложенное открытие графика (для избежания конфликта с двойным кликом)"""
        click_logger.info(f"=== DELAYED CHART OPEN for {token_name} ===")
        # Проверяем, не было ли двойного клика
        if hasattr(self, '_double_click_handled') and self._double_click_handled:
            click_logger.info(f"Double click was handled, skipping single click for {token_name}")
            self._double_click_handled = False
            return
        
        click_logger.info(f"Looking for token {token_name} in tokens_data...")
        click_logger.info(f"Available tokens: {[t['name'] for t in self.tokens_data]}")
        
        # Находим токен в данных
        token_found = False
        for token in self.tokens_data:
            if token['name'] == token_name:
                click_logger.info(f"Found token data for {token_name}: {token}")
                # Открываем график при одинарном клике
                click_logger.info(f"Opening chart for {token_name}...")
                self.open_chart_for_token(token)
                token_found = True
                break
        
        if not token_found:
            click_logger.error(f"Token {token_name} not found in tokens_data!")
            click_logger.error(f"Available tokens: {[t['name'] for t in self.tokens_data]}")
    
    def on_spread_table_double_click(self, event):
        """Обработка двойного клика по таблице спредов"""
        click_logger.info("=== DOUBLE CLICK EVENT ===")
        # Устанавливаем флаг, что двойной клик был обработан
        self._double_click_handled = True
        click_logger.info("Set _double_click_handled = True")
        
        item = self.spread_tree.selection()[0] if self.spread_tree.selection() else None
        click_logger.info(f"Double click item: {item}")
        if item:
            values = self.spread_tree.item(item, 'values')
            click_logger.info(f"Double click values: {values}")
            
            if values:
                token_name = values[0]
                click_logger.info(f"Double click on token: {token_name}")
                
                # Получаем теги элемента
                tags = self.spread_tree.item(item, 'tags')
                click_logger.info(f"Double click tags: {tags}")
                
                # Находим токен в данных
                token_found = False
                for token in self.tokens_data:
                    if token['name'] == token_name:
                        click_logger.info(f"Found token data for {token_name}: {token}")
                        click_logger.info(f"Opening chart for {token_name}...")
                        self.open_chart_for_token(token)
                        token_found = True
                        break
                
                if not token_found:
                    click_logger.error(f"Token {token_name} not found in tokens_data!")
                    click_logger.error(f"Available tokens: {[t['name'] for t in self.tokens_data]}")
    

    def refresh_tokens(self):
        """Перечитать tokens.json и обновить выпадающий список без перезапуска"""
        try:
            previous_selection = self.token_combobox.get() if hasattr(self, 'token_combobox') else ''
            if os.path.exists('tokens.json'):
                with open('tokens.json', 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.tokens_data = data.get('tokens', [])
            else:
                self.tokens_data = []
            self.update_token_combobox()
            # восстановить выбор, если он всё ещё существует
            token_names = [f"{t['name']} ({t['chain']})" for t in self.tokens_data]
            if previous_selection in token_names:
                self.token_combobox.set(previous_selection)
            
            # Обновляем таблицу спредов
            if hasattr(self, 'spread_tree'):
                self.update_spread_table()
            
            self.status_label.config(text="Tokens reloaded")
            logger.info("Tokens reloaded from tokens.json")
        except Exception as e:
            logger.error(f"Error refreshing tokens: {e}")
            messagebox.showerror("Error", f"Failed to refresh tokens: {str(e)}")
    
    def add_token(self):
        """Добавление нового токена"""
        dialog = TokenDialog(self.root)
        if dialog.result:
            token = dialog.result
            self.tokens_data.append(token)
            self.save_tokens()
            self.update_token_combobox()
            messagebox.showinfo("Success", f"Token {token['name']} added successfully!")
    
    def remove_token(self):
        """Удаление выбранного токена"""
        selected = self.token_combobox.get()
        if not selected:
            messagebox.showwarning("Warning", "Please select a token to remove")
            return
        
        if messagebox.askyesno("Confirm", f"Are you sure you want to remove {selected}?"):
            self.tokens_data = [t for t in self.tokens_data if f"{t['name']} ({t['chain']})" != selected]
            self.save_tokens()
            self.update_token_combobox()
            messagebox.showinfo("Success", "Token removed successfully!")
    
    def update_token_combobox(self):
        """Обновление списка токенов в комбобоксе"""
        token_names = [f"{token['name']} ({token['chain']})" for token in self.tokens_data]
        self.token_combobox['values'] = token_names
        if token_names:
            self.token_combobox.set(token_names[0])
        
    def setup_ui(self):
        # Главный фрейм
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Настройка растягивания
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        
        # Заголовок
        title_label = ttk.Label(main_frame, text="Hybrid Price Chart", 
                               font=('Arial', 16, 'bold'))
        title_label.grid(row=0, column=0, columnspan=3, pady=(0, 20))
        
        # Выбор токена
        ttk.Label(main_frame, text="Select Token:", 
                 font=('Arial', 10, 'bold')).grid(row=1, column=0, sticky=tk.W, pady=5)
        
        token_frame = ttk.Frame(main_frame)
        token_frame.grid(row=1, column=1, sticky=(tk.W, tk.E), pady=5, padx=(10, 0))
        token_frame.columnconfigure(0, weight=1)
        
        self.token_combobox = ttk.Combobox(token_frame, width=47, font=('Arial', 10), state="readonly")
        self.token_combobox.grid(row=0, column=0, sticky=(tk.W, tk.E))
        
        # Кнопки управления токенами
        token_buttons_frame = ttk.Frame(main_frame)
        token_buttons_frame.grid(row=2, column=1, sticky=tk.W, pady=5, padx=(10, 0))
        
        ttk.Button(token_buttons_frame, text="+ Add Token", 
                  command=self.add_token).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(token_buttons_frame, text="- Remove Token", 
                  command=self.remove_token).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(token_buttons_frame, text="↻ Refresh", 
                  command=self.refresh_tokens).pack(side=tk.LEFT)
        
        # Инициализируем список токенов
        self.update_token_combobox()
        
        # Кнопки управления графиками
        chart_buttons_frame = ttk.Frame(main_frame)
        chart_buttons_frame.grid(row=3, column=0, columnspan=3, pady=10)
        
        
        
        # Кнопки управления мониторингом
        monitor_buttons_frame = ttk.Frame(main_frame)
        monitor_buttons_frame.grid(row=4, column=0, columnspan=3, pady=10)
        
        self.start_monitor_button = ttk.Button(monitor_buttons_frame, text="Start Background Monitor ✓", 
                                             command=self.start_background_monitoring)
        self.start_monitor_button.pack(side=tk.LEFT, padx=5)
        
        self.stop_monitor_button = ttk.Button(monitor_buttons_frame, text="Stop Background Monitor", 
                                            command=self.stop_background_monitoring)
        self.stop_monitor_button.pack(side=tk.LEFT, padx=5)
        
        self.monitor_settings_button = ttk.Button(monitor_buttons_frame, text="Monitor Settings", 
                                                command=self.open_monitor_settings)
        self.monitor_settings_button.pack(side=tk.LEFT, padx=5)
        
        
        # Инициализируем кнопку с правильным текстом в зависимости от текущего состояния
        alert_button_text = "🔔 Enable Alerts" if self.background_monitor.disable_alerts else "🔕 Disable Alerts"
        self.toggle_alerts_button = ttk.Button(monitor_buttons_frame, text=alert_button_text, 
                                              command=self.toggle_alerts)
        self.toggle_alerts_button.pack(side=tk.LEFT, padx=5)
        
        self.blacklist_button = ttk.Button(monitor_buttons_frame, text="🚫 Blacklist", 
                                          command=self.open_blacklist_dialog)
        self.blacklist_button.pack(side=tk.LEFT, padx=5)
        
        # Кнопка "Always On Top" для окон графиков
        self.always_on_top_button = ttk.Button(monitor_buttons_frame, text="📌 Charts: Always On Top ✓", 
                                              command=self.toggle_charts_always_on_top)
        self.always_on_top_button.pack(side=tk.LEFT, padx=5)
        
        
        
        # Панель мониторинга спредов в реальном времени
        spread_frame = ttk.LabelFrame(main_frame, text="Real-time Spreads Monitor", padding="10")
        spread_frame.grid(row=6, column=0, columnspan=3, sticky=(tk.W, tk.E, tk.N, tk.S), pady=10)
        
        # Информационная строка
        spread_buttons_frame = ttk.Frame(spread_frame)
        spread_buttons_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(spread_buttons_frame, text="💡 Click on any row to open chart and copy ticker to clipboard", 
                 font=('Arial', 9), foreground='gray').pack(side=tk.LEFT)
        
        # Создаем Treeview для отображения спредов
        columns = ('Token', 'Chain', 'CEX Price', 'DEX Price', 'Spread %', 'Status')
        self.spread_tree = ttk.Treeview(spread_frame, columns=columns, show='headings', height=8)
        
        # Настройка колонок
        self.spread_tree.heading('Token', text='Token')
        self.spread_tree.heading('Chain', text='Chain')
        self.spread_tree.heading('CEX Price', text='CEX Price')
        self.spread_tree.heading('DEX Price', text='DEX Price')
        self.spread_tree.heading('Spread %', text='Spread % (sorted by abs value)')
        self.spread_tree.heading('Status', text='Status')
        
        # Ширина колонок
        self.spread_tree.column('Token', width=120)
        self.spread_tree.column('Chain', width=80)
        self.spread_tree.column('CEX Price', width=100)
        self.spread_tree.column('DEX Price', width=100)
        self.spread_tree.column('Spread %', width=80)
        self.spread_tree.column('Status', width=100)
        
        # Скроллбар для таблицы
        scrollbar = ttk.Scrollbar(spread_frame, orient=tk.VERTICAL, command=self.spread_tree.yview)
        self.spread_tree.configure(yscrollcommand=scrollbar.set)
        
        # Размещение элементов
        self.spread_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Привязка одинарного и двойного клика для открытия графика
        self.spread_tree.bind('<Button-1>', self.on_spread_table_single_click)
        self.spread_tree.bind('<Double-1>', self.on_spread_table_double_click)
        
        # Инициализируем таблицу токенами
        self.update_spread_table()
        
        # Статус
        self.status_label = ttk.Label(main_frame, text="Ready to start chart", 
                                     font=('Arial', 10))
        self.status_label.grid(row=7, column=0, columnspan=3, pady=10)
        
        # Привязка Enter к комбобоксу
        # Горячая клавиша обновления
        self.root.bind('<F5>', lambda e: self.refresh_tokens())
    
        
    
    def zoom_in(self, chart):
        """Приближение графика"""
        try:
            xlim = chart.ax.get_xlim()
            ylim = chart.ax.get_ylim()
            
            # Уменьшаем диапазон на 20%
            x_center = (xlim[0] + xlim[1]) / 2
            y_center = (ylim[0] + ylim[1]) / 2
            x_range = (xlim[1] - xlim[0]) * 0.8
            y_range = (ylim[1] - ylim[0]) * 0.8
            
            new_xlim = [x_center - x_range/2, x_center + x_range/2]
            new_ylim = [y_center - y_range/2, y_center + y_range/2]
            
            chart.ax.set_xlim(new_xlim)
            chart.ax.set_ylim(new_ylim)
            
            # Устанавливаем флаг ручного управления
            chart.manual_zoom = True
            chart.manual_xlim = new_xlim
            chart.manual_ylim = new_ylim
            
            chart.fig.canvas.draw()
        except Exception as e:
            logger.error(f"Error zooming in: {e}")
    
    def zoom_out(self, chart):
        """Отдаление графика"""
        try:
            xlim = chart.ax.get_xlim()
            ylim = chart.ax.get_ylim()
            
            # Увеличиваем диапазон на 25%
            x_center = (xlim[0] + xlim[1]) / 2
            y_center = (ylim[0] + ylim[1]) / 2
            x_range = (xlim[1] - xlim[0]) * 1.25
            y_range = (ylim[1] - ylim[0]) * 1.25
            
            new_xlim = [x_center - x_range/2, x_center + x_range/2]
            new_ylim = [y_center - y_range/2, y_center + y_range/2]
            
            chart.ax.set_xlim(new_xlim)
            chart.ax.set_ylim(new_ylim)
            
            # Устанавливаем флаг ручного управления
            chart.manual_zoom = True
            chart.manual_xlim = new_xlim
            chart.manual_ylim = new_ylim
            
            chart.fig.canvas.draw()
        except Exception as e:
            logger.error(f"Error zooming out: {e}")
    
    def reset_zoom(self, chart):
        """Сброс масштаба к автоматическому"""
        try:
            chart.reset_zoom()
        except Exception as e:
            logger.error(f"Error resetting zoom: {e}")
    
    def stop_chart(self, chart, window):
        """Остановка графика"""
        try:
            logger.info(f"Stopping chart: {chart}")
            chart.stop()
            
            # Удаляем из списка активных графиков
            self.charts = [(c, w) for c, w in self.charts if w != window]
            
            # Закрываем окно
            window.destroy()
            
            self.status_label.config(text="Chart stopped")
            logger.info("Chart stopped successfully")
        except Exception as e:
            logger.error(f"Error stopping chart: {e}")
            messagebox.showerror("Error", f"Error stopping chart: {str(e)}")
    
    def create_chart_sliders(self, parent_frame, chart):
        """Создание ползунков для управления графиком с организацией по вкладкам"""
        # Инициализируем переменные для ползунков
        chart.slider_vars = {}
        chart.slider_labels = {}  # Для отображения значений
        
        # Настройки по умолчанию (можно изменить)
        default_values = {
            # Основные настройки из chart_settings.json
            'line_opacity': 0.9,
            'line_width': 1.28125,
            'marker_size': 8.0,
            'fill_opacity': 0.2,
            'font_size': 8.0,
            'grid_alpha': 0.2,
            'animation_speed': 30.0,
            'y_margin': 0.5,
            'spread_brightness': 1.0,
            'x_margin': 0.0,
            'title_size': 16.0,
            'legend_size': 11.0,
            'axis_label_size': 12.0,
            'tick_size': 10.0,
            'line_style_alpha': 0.8,
            'background_alpha': 0.1,
            'border_width': 1.0,
            'data_point_size': 6.0,
            'trend_line_width': 2.0,
            'volume_alpha': 0.3,
            
            # Цвета CEX линии
            'cex_color_red': 0.0,
            'cex_color_green': 1.0,
            'cex_color_blue': 0.0,
            
            # Цвета DEX линии
            'dex_color_red': 1.0,
            'dex_color_green': 0.0,
            'dex_color_blue': 0.0,
            
            # Настройки сетки
            'grid_line_width': 0.5,
            'grid_line_style': 0.0,
            
            # Цвета границ
            'spine_color_red': 1.0,
            'spine_color_green': 1.0,
            'spine_color_blue': 1.0,
            
            # Цвета текста
            'text_color_red': 1.0,
            'text_color_green': 1.0,
            'text_color_blue': 1.0,
            
            # Настройки маркеров
            'marker_edge_width': 2.0,
            'marker_alpha': 1.0,
            
            # Цвета заливки
            'fill_color_red': 0.0,
            'fill_color_green': 1.0,
            'fill_color_blue': 0.0,
            
            # Цвета заголовка
            'title_color_red': 1.0,
            'title_color_green': 1.0,
            'title_color_blue': 1.0,
            
            # Настройки легенды
            'legend_alpha': 0.8,
            'legend_frame_width': 1.0,
            
            # Настройки делений
            'axis_ticks_length': 4.0,
            'axis_ticks_width': 1.0,
            'axis_ticks_direction': 0.0,
            'minor_ticks_alpha': 0.3,
            'major_ticks_alpha': 1.0,
            'tick_label_pad': 3.0,
            'axis_label_pad': 10.0,
            'title_pad': 20.0,
            'legend_pad': 0.0,
            
            # Размеры элементов из chart_settings.json
            'figure_width': 6.375,
            'figure_height': 4.0,
            'figure_dpi': 100.0,
            'chart_area_width': 0.67,
            'chart_area_height': 0.7,
            'legend_box_width': 0.15,
            'legend_box_height': 0.1,
            'title_box_width': 0.8,
            'title_box_height': 0.05,
            'axis_label_box_width': 0.1,
            'axis_label_box_height': 0.05,
            'tick_label_box_width': 0.08,
            'tick_label_box_height': 0.03,
            'marker_box_size': 0.01,
            'grid_cell_width': 0.1,
            'grid_cell_height': 0.1,
            'spread_box_width': 0.12,
            'spread_box_height': 0.04,
            'price_label_width': 0.07916666666666666,
            'price_label_height': 0.03,
            'volume_bar_width': 0.8,
            'volume_bar_height': 0.2,
            'trend_line_box_width': 0.1,
            'trend_line_box_height': 0.02,
            'background_box_width': 1.0,
            'background_box_height': 1.0,
            'border_box_width': 0.02,
            'border_box_height': 0.02,
            'guide_line_width': 0.3,
            'guide_line_height': 0.01,
            'badge_width': 0.11,
            'badge_height': 0.02,
            'progress_bar_width': 0.2,
            'progress_bar_height': 0.01,
            'status_text_width': 0.15,
            'status_text_height': 0.02,
            'time_label_width': 0.1,
            'time_label_height': 0.02,
            'spread_arrow_width': 0.05,
            'spread_arrow_height': 0.02,
            'price_change_width': 0.08,
            'price_change_height': 0.02,
            'volume_text_width': 0.06,
            'volume_text_height': 0.02,
            'axis_arrow_width': 0.02,
            'axis_arrow_height': 0.01,
            'grid_major_width': 0.8,
            'grid_major_height': 0.01,
            'grid_minor_width': 0.4,
            'grid_minor_height': 0.005,
            'tick_major_width': 0.01,
            'tick_major_height': 0.02,
            'tick_minor_width': 0.005,
            'tick_minor_height': 0.01,
            'spine_width': 0.01,
            'spine_height': 0.8,
            'corner_radius': 0.01,
            'shadow_width': 0.02,
            'shadow_height': 0.02,
            'glow_width': 0.05,
            'glow_height': 0.05,
            'highlight_width': 0.1,
            'highlight_height': 0.05,
            'selection_box_width': 0.2,
            'selection_box_height': 0.1,
            'zoom_box_width': 0.15,
            'zoom_box_height': 0.08,
            'pan_handle_width': 0.03,
            'pan_handle_height': 0.03,
            'scroll_bar_width': 0.02,
            'scroll_bar_height': 0.1,
            'resize_handle_width': 0.02,
            'resize_handle_height': 0.02,
            'close_button_width': 0.02,
            'close_button_height': 0.02,
            'minimize_button_width': 0.02,
            'minimize_button_height': 0.01,
            'maximize_button_width': 0.02,
            'maximize_button_height': 0.02,
            'menu_bar_width': 0.8,
            'menu_bar_height': 0.03,
            'toolbar_width': 0.6,
            'toolbar_height': 0.04,
            'status_bar_width': 0.8,
            'status_bar_height': 0.02,
            'side_panel_width': 0.2,
            'side_panel_height': 0.8,
            'bottom_panel_width': 0.8,
            'bottom_panel_height': 0.15,
            'top_panel_width': 0.8,
            'top_panel_height': 0.1,
            'left_panel_width': 0.15,
            'left_panel_height': 0.8,
            'right_panel_width': 0.15,
            'right_panel_height': 0.8,
            'center_panel_width': 0.7,
            'center_panel_height': 0.7,
            'overlay_width': 0.8,
            'overlay_height': 0.8,
            'popup_width': 0.3,
            'popup_height': 0.2,
            'dialog_width': 0.4,
            'dialog_height': 0.3,
            'modal_width': 0.5,
            'modal_height': 0.4,
            'tooltip_width': 0.15,
            'tooltip_height': 0.05,
            'notification_width': 0.25,
            'notification_height': 0.08,
            'progress_indicator_width': 0.1,
            'progress_indicator_height': 0.01,
            'loading_spinner_width': 0.05,
            'loading_spinner_height': 0.05,
            'error_message_width': 0.3,
            'error_message_height': 0.1,
            'success_message_width': 0.25,
            'success_message_height': 0.08,
            'warning_message_width': 0.28,
            'warning_message_height': 0.09,
            'info_message_width': 0.2,
            'info_message_height': 0.06,
            'debug_panel_width': 0.4,
            'debug_panel_height': 0.3,
            'console_width': 0.6,
            'console_height': 0.4,
            'log_viewer_width': 0.5,
            'log_viewer_height': 0.5,
            'settings_panel_width': 0.35,
            'settings_panel_height': 0.6,
            'preferences_width': 0.4,
            'preferences_height': 0.7,
            'about_dialog_width': 0.3,
            'about_dialog_height': 0.4,
            'help_panel_width': 0.45,
            'help_panel_height': 0.55,
            'tutorial_overlay_width': 0.6,
            'tutorial_overlay_height': 0.5,
            'welcome_screen_width': 0.7,
            'welcome_screen_height': 0.6,
            'splash_screen_width': 0.4,
            'splash_screen_height': 0.3,
            'loading_screen_width': 0.5,
            'loading_screen_height': 0.4,
            'error_screen_width': 0.6,
            'error_screen_height': 0.5,
            'main_window_width': 1.0,
            'main_window_height': 1.0,
            'chart_window_width': 0.8,
            'chart_window_height': 0.8,
            'control_panel_width': 0.25,
            'control_panel_height': 0.9,
            'data_panel_width': 0.3,
            'data_panel_height': 0.7,
            'analysis_panel_width': 0.35,
            'analysis_panel_height': 0.6,
            'trading_panel_width': 0.1,
            'trading_panel_height': 0.8,
            'portfolio_panel_width': 0.32,
            'portfolio_panel_height': 0.75,
            'news_panel_width': 0.4,
            'news_panel_height': 0.5,
            'alerts_panel_width': 0.3,
            'alerts_panel_height': 0.6,
            'history_panel_width': 0.45,
            'history_panel_height': 0.7,
            'favorites_panel_width': 0.25,
            'favorites_panel_height': 0.8,
            'watchlist_panel_width': 0.3,
            'watchlist_panel_height': 0.9,
            'market_panel_width': 0.5,
            'market_panel_height': 0.6,
            'orderbook_panel_width': 0.35,
            'orderbook_panel_height': 0.7,
            'trades_panel_width': 0.4,
            'trades_panel_height': 0.5,
            'depth_panel_width': 0.38,
            'depth_panel_height': 0.65,
            'volume_panel_width': 0.3,
            'volume_panel_height': 0.4,
            'indicators_panel_width': 0.32,
            'indicators_panel_height': 0.8,
            'tools_panel_width': 0.28,
            'tools_panel_height': 0.7,
            'drawing_panel_width': 0.25,
            'drawing_panel_height': 0.6,
            'measurement_panel_width': 0.3,
            'measurement_panel_height': 0.5,
            'annotation_panel_width': 0.35,
            'annotation_panel_height': 0.55,
            'export_panel_width': 0.4,
            'export_panel_height': 0.45,
            'import_panel_width': 0.42,
            'import_panel_height': 0.48,
            'backup_panel_width': 0.38,
            'backup_panel_height': 0.52,
            'restore_panel_width': 0.4,
            'restore_panel_height': 0.5,
            'sync_panel_width': 0.35,
            'sync_panel_height': 0.4,
            'update_panel_width': 0.3,
            'update_panel_height': 0.35,
            'install_panel_width': 0.45,
            'install_panel_height': 0.6,
            'uninstall_panel_width': 0.4,
            'uninstall_panel_height': 0.55,
            'config_panel_width': 0.5,
            'config_panel_height': 0.7,
            'advanced_panel_width': 0.6,
            'advanced_panel_height': 0.8,
            'expert_panel_width': 0.7,
            'expert_panel_height': 0.9,
            'developer_panel_width': 0.8,
            'developer_panel_height': 1.0,
            'admin_panel_width': 0.9,
            'admin_panel_height': 1.0,
            'user_panel_width': 0.3,
            'user_panel_height': 0.6,
            'profile_panel_width': 0.35,
            'profile_panel_height': 0.65,
            'account_panel_width': 0.4,
            'account_panel_height': 0.7,
            'security_panel_width': 0.45,
            'security_panel_height': 0.75,
            'privacy_panel_width': 0.5,
            'privacy_panel_height': 0.8,
            'permissions_panel_width': 0.55,
            'permissions_panel_height': 0.85,
            'subscription_panel_width': 0.6,
            'subscription_panel_height': 0.9,
            'billing_panel_width': 0.65,
            'billing_panel_height': 0.95,
            'payment_panel_width': 0.7,
            'payment_panel_height': 1.0,
            'invoice_panel_width': 0.75,
            'invoice_panel_height': 1.0,
            'receipt_panel_width': 0.8,
            'receipt_panel_height': 1.0,
            'statement_panel_width': 0.85,
            'statement_panel_height': 1.0,
            'report_panel_width': 0.9,
            'report_panel_height': 1.0,
            'analytics_panel_width': 0.95,
            'analytics_panel_height': 1.0,
            'dashboard_panel_width': 1.0,
            'dashboard_panel_height': 1.0,
            
            # Цвета фона
            'figure_edge_color_red': 0.0,
            'figure_edge_color_green': 0.0,
            'figure_edge_color_blue': 0.0,
            'figure_face_color_red': 0.0,
            'figure_face_color_green': 0.0,
            'figure_face_color_blue': 0.0,
            
            # Настройки subplot
            'subplot_adjust_left': 0.1,
            'subplot_adjust_right': 0.9,
            'subplot_adjust_top': 0.9,
            'subplot_adjust_bottom': 0.1,
            'subplot_adjust_wspace': 0.2,
            'subplot_adjust_hspace': 0.2
        }
        
        # Создаем систему вкладок для организации ползунков
        notebook = ttk.Notebook(parent_frame)
        notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Вкладка 1: Основные настройки
        basic_frame = ttk.Frame(notebook)
        notebook.add(basic_frame, text="🎨 Основные")
        
        # Вкладка 2: Цвета
        colors_frame = ttk.Frame(notebook)
        notebook.add(colors_frame, text="🌈 Цвета")
        
        # Вкладка 3: Размеры элементов
        sizes_frame = ttk.Frame(notebook)
        notebook.add(sizes_frame, text="📏 Размеры")
        
        # Вкладка 4: Размеры панелей
        panels_frame = ttk.Frame(notebook)
        notebook.add(panels_frame, text="🖼️ Панели")
        
        # Вкладка 5: Размеры окон
        windows_frame = ttk.Frame(notebook)
        notebook.add(windows_frame, text="🪟 Окна")
        
        # Вкладка 6: Размеры сообщений
        messages_frame = ttk.Frame(notebook)
        notebook.add(messages_frame, text="💬 Сообщения")
        
        # Вкладка 7: Размеры торговых элементов
        trading_frame = ttk.Frame(notebook)
        notebook.add(trading_frame, text="📈 Торговля")
        
        # Вкладка 8: Размеры системных элементов
        system_frame = ttk.Frame(notebook)
        notebook.add(system_frame, text="⚙️ Система")
        
        # === ВКЛАДКА 1: ОСНОВНЫЕ НАСТРОЙКИ ===
        self.create_basic_sliders(basic_frame, chart, default_values)
        
        # === ВКЛАДКА 2: ЦВЕТА ===
        self.create_color_sliders(colors_frame, chart, default_values)
        
        # === ВКЛАДКА 3: РАЗМЕРЫ ЭЛЕМЕНТОВ ===
        self.create_size_sliders(sizes_frame, chart, default_values)
        
        # === ВКЛАДКА 4: РАЗМЕРЫ ПАНЕЛЕЙ ===
        self.create_panel_sliders(panels_frame, chart, default_values)
        
        # === ВКЛАДКА 5: РАЗМЕРЫ ОКОН ===
        self.create_window_sliders(windows_frame, chart, default_values)
        
        # === ВКЛАДКА 6: РАЗМЕРЫ СООБЩЕНИЙ ===
        self.create_message_sliders(messages_frame, chart, default_values)
        
        # === ВКЛАДКА 7: РАЗМЕРЫ ТОРГОВЫХ ЭЛЕМЕНТОВ ===
        self.create_trading_sliders(trading_frame, chart, default_values)
        
        # === ВКЛАДКА 8: РАЗМЕРЫ СИСТЕМНЫХ ЭЛЕМЕНТОВ ===
        self.create_system_sliders(system_frame, chart, default_values)
        
        # Кнопки управления (внизу всех вкладок)
        buttons_frame = ttk.Frame(parent_frame)
        buttons_frame.pack(fill=tk.X, pady=10)
        
        # Кнопка сброса всех настроек
        reset_button = ttk.Button(buttons_frame, text="🔄 Reset All Settings", 
                                 command=lambda: self.reset_all_sliders(chart))
        reset_button.pack(side=tk.LEFT, padx=5)
        
        # Кнопка сохранения настроек
        save_button = ttk.Button(buttons_frame, text="💾 Save Settings", 
                                command=lambda: self.save_slider_settings(chart))
        save_button.pack(side=tk.LEFT, padx=5)
        
        # Кнопка загрузки настроек
        load_button = ttk.Button(buttons_frame, text="📁 Load Settings", 
                                command=lambda: self.load_slider_settings(chart))
        load_button.pack(side=tk.LEFT, padx=5)
        
        # Применяем настройки по умолчанию сразу после создания слайдеров
        self.apply_default_settings(chart)
    
    def apply_default_settings(self, chart):
        """Применение настроек по умолчанию к графику"""
        try:
            # Применяем все настройки по умолчанию
            self.update_line_opacity(chart)
            self.update_line_width(chart)
            self.update_marker_size(chart)
            self.update_fill_opacity(chart)
            self.update_font_size(chart)
            self.update_grid_alpha(chart)
            self.update_title_size(chart)
            self.update_y_margin(chart)
            self.update_x_margin(chart)
            self.update_axis_label_size(chart)
            self.update_tick_size(chart)
            self.update_animation_speed(chart)
            self.update_spread_brightness(chart)
            self.update_line_style_alpha(chart)
            self.update_background_alpha(chart)
            self.update_border_width(chart)
            self.update_data_point_size(chart)
            self.update_trend_line_width(chart)
            self.update_volume_alpha(chart)
            # Цвета
            self.update_cex_color(chart)
            self.update_dex_color(chart)
            self.update_grid_line_width(chart)
            self.update_grid_line_style(chart)
            self.update_spine_color(chart)
            self.update_text_color(chart)
            self.update_marker_edge_width(chart)
            self.update_marker_alpha(chart)
            self.update_fill_color(chart)
            self.update_title_color(chart)
            # Размеры
            self.update_axis_ticks_length(chart)
            self.update_axis_ticks_width(chart)
            self.update_axis_ticks_direction(chart)
            self.update_minor_ticks_alpha(chart)
            self.update_major_ticks_alpha(chart)
            self.update_tick_label_pad(chart)
            self.update_axis_label_pad(chart)
            self.update_title_pad(chart)
            # Функции размеров
            self.update_figure_width(chart)
            self.update_figure_height(chart)
            self.update_chart_area_width(chart)
            self.update_title_box_width(chart)
            self.update_marker_box_size(chart)
            self.update_grid_cell_width(chart)
            self.update_grid_cell_height(chart)
            self.update_grid_major_width(chart)
            self.update_side_panel_width(chart)
            self.update_bottom_panel_height(chart)
            self.update_top_panel_height(chart)
            self.update_trading_panel_width(chart)
            self.update_portfolio_panel_width(chart)
            self.update_orderbook_panel_width(chart)
            self.update_main_window_width(chart)
            self.update_chart_window_width(chart)
            self.update_control_panel_width(chart)
            self.update_dialog_width(chart)
            self.update_modal_width(chart)
            self.update_popup_width(chart)
            self.update_notification_width(chart)
            self.update_tooltip_width(chart)
            self.update_success_message_width(chart)
            self.update_error_message_width(chart)
            self.update_warning_message_width(chart)
            self.update_info_message_width(chart)
            self.update_spread_box_width(chart)
            self.update_price_label_width(chart)
            self.update_volume_bar_width(chart)
            self.update_trend_line_box_width(chart)
            self.update_badge_width(chart)  # Это применит badge_width = 0.11
            self.update_progress_bar_width(chart)
            self.update_menu_bar_width(chart)
            self.update_toolbar_width(chart)
            self.update_status_bar_width(chart)
            self.update_close_button_width(chart)
            self.update_minimize_button_width(chart)
            self.update_maximize_button_width(chart)
            
            logger.info("Default settings applied to chart")
        except Exception as e:
            logger.error(f"Error applying default settings: {e}")
    
    def create_basic_sliders(self, parent_frame, chart, default_values):
        """Создание ползунков основных настроек"""
        # Создаем ScrollableFrame для прокрутки
        canvas = tk.Canvas(parent_frame)
        scrollbar = ttk.Scrollbar(parent_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Основные настройки линий
        row1 = ttk.Frame(scrollable_frame)
        row1.pack(fill=tk.X, pady=5)
        self.create_slider_with_value(row1, "Line Opacity:", 'line_opacity', 0.1, 1.0, 
                                    default_values['line_opacity'], chart, self.update_line_opacity)
        self.create_slider_with_value(row1, "Line Width:", 'line_width', 0.5, 8.0, 
                                    default_values['line_width'], chart, self.update_line_width)
        self.create_slider_with_value(row1, "Marker Size:", 'marker_size', 2.0, 20.0, 
                                    default_values['marker_size'], chart, self.update_marker_size)
        
        # Настройки текста и сетки
        row2 = ttk.Frame(scrollable_frame)
        row2.pack(fill=tk.X, pady=5)
        self.create_slider_with_value(row2, "Font Size:", 'font_size', 6.0, 20.0, 
                                    default_values['font_size'], chart, self.update_font_size)
        self.create_slider_with_value(row2, "Grid Alpha:", 'grid_alpha', 0.0, 1.0, 
                                    default_values['grid_alpha'], chart, self.update_grid_alpha)
        self.create_slider_with_value(row2, "Title Size:", 'title_size', 8.0, 24.0, 
                                    default_values['title_size'], chart, self.update_title_size)
        
        # Настройки осей
        row3 = ttk.Frame(scrollable_frame)
        row3.pack(fill=tk.X, pady=5)
        self.create_slider_with_value(row3, "Y Margin:", 'y_margin', 0.0, 1.0, 
                                    default_values['y_margin'], chart, self.update_y_margin)
        self.create_slider_with_value(row3, "X Margin:", 'x_margin', 0.0, 0.5, 
                                    default_values['x_margin'], chart, self.update_x_margin)
        self.create_slider_with_value(row3, "Animation Speed:", 'animation_speed', 10.0, 200.0, 
                                    default_values['animation_speed'], chart, self.update_animation_speed)
        
        # Дополнительные настройки
        row4 = ttk.Frame(scrollable_frame)
        row4.pack(fill=tk.X, pady=5)
        self.create_slider_with_value(row4, "Fill Opacity:", 'fill_opacity', 0.0, 0.8, 
                                    default_values['fill_opacity'], chart, self.update_fill_opacity)
        self.create_slider_with_value(row4, "Background Alpha:", 'background_alpha', 0.0, 0.5, 
                                    default_values['background_alpha'], chart, self.update_background_alpha)
        self.create_slider_with_value(row4, "Border Width:", 'border_width', 0.5, 3.0, 
                                    default_values['border_width'], chart, self.update_border_width)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
    
    def create_color_sliders(self, parent_frame, chart, default_values):
        """Создание ползунков цветов"""
        canvas = tk.Canvas(parent_frame)
        scrollbar = ttk.Scrollbar(parent_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Цвета CEX линии
        row1 = ttk.Frame(scrollable_frame)
        row1.pack(fill=tk.X, pady=5)
        ttk.Label(row1, text="CEX Line Colors:", font=("Arial", 10, "bold")).pack()
        row1_cex = ttk.Frame(scrollable_frame)
        row1_cex.pack(fill=tk.X, pady=2)
        self.create_slider_with_value(row1_cex, "CEX Red:", 'cex_color_red', 0.0, 1.0, 
                                    default_values['cex_color_red'], chart, self.update_cex_color)
        self.create_slider_with_value(row1_cex, "CEX Green:", 'cex_color_green', 0.0, 1.0, 
                                    default_values['cex_color_green'], chart, self.update_cex_color)
        self.create_slider_with_value(row1_cex, "CEX Blue:", 'cex_color_blue', 0.0, 1.0, 
                                    default_values['cex_color_blue'], chart, self.update_cex_color)
        
        # Цвета DEX линии
        row2 = ttk.Frame(scrollable_frame)
        row2.pack(fill=tk.X, pady=5)
        ttk.Label(row2, text="DEX Line Colors:", font=("Arial", 10, "bold")).pack()
        row2_dex = ttk.Frame(scrollable_frame)
        row2_dex.pack(fill=tk.X, pady=2)
        self.create_slider_with_value(row2_dex, "DEX Red:", 'dex_color_red', 0.0, 1.0, 
                                    default_values['dex_color_red'], chart, self.update_dex_color)
        self.create_slider_with_value(row2_dex, "DEX Green:", 'dex_color_green', 0.0, 1.0, 
                                    default_values['dex_color_green'], chart, self.update_dex_color)
        self.create_slider_with_value(row2_dex, "DEX Blue:", 'dex_color_blue', 0.0, 1.0, 
                                    default_values['dex_color_blue'], chart, self.update_dex_color)
        
        # Цвета границ
        row3 = ttk.Frame(scrollable_frame)
        row3.pack(fill=tk.X, pady=5)
        ttk.Label(row3, text="Border Colors:", font=("Arial", 10, "bold")).pack()
        row3_spine = ttk.Frame(scrollable_frame)
        row3_spine.pack(fill=tk.X, pady=2)
        self.create_slider_with_value(row3_spine, "Spine Red:", 'spine_color_red', 0.0, 1.0, 
                                    default_values['spine_color_red'], chart, self.update_spine_color)
        self.create_slider_with_value(row3_spine, "Spine Green:", 'spine_color_green', 0.0, 1.0, 
                                    default_values['spine_color_green'], chart, self.update_spine_color)
        self.create_slider_with_value(row3_spine, "Spine Blue:", 'spine_color_blue', 0.0, 1.0, 
                                    default_values['spine_color_blue'], chart, self.update_spine_color)
        
        # Цвета текста
        row4 = ttk.Frame(scrollable_frame)
        row4.pack(fill=tk.X, pady=5)
        ttk.Label(row4, text="Text Colors:", font=("Arial", 10, "bold")).pack()
        row4_text = ttk.Frame(scrollable_frame)
        row4_text.pack(fill=tk.X, pady=2)
        self.create_slider_with_value(row4_text, "Text Red:", 'text_color_red', 0.0, 1.0, 
                                    default_values['text_color_red'], chart, self.update_text_color)
        self.create_slider_with_value(row4_text, "Text Green:", 'text_color_green', 0.0, 1.0, 
                                    default_values['text_color_green'], chart, self.update_text_color)
        self.create_slider_with_value(row4_text, "Text Blue:", 'text_color_blue', 0.0, 1.0, 
                                    default_values['text_color_blue'], chart, self.update_text_color)
        
        # Цвета заливки
        row5 = ttk.Frame(scrollable_frame)
        row5.pack(fill=tk.X, pady=5)
        ttk.Label(row5, text="Fill Colors:", font=("Arial", 10, "bold")).pack()
        row5_fill = ttk.Frame(scrollable_frame)
        row5_fill.pack(fill=tk.X, pady=2)
        self.create_slider_with_value(row5_fill, "Fill Red:", 'fill_color_red', 0.0, 1.0, 
                                    default_values['fill_color_red'], chart, self.update_fill_color)
        self.create_slider_with_value(row5_fill, "Fill Green:", 'fill_color_green', 0.0, 1.0, 
                                    default_values['fill_color_green'], chart, self.update_fill_color)
        self.create_slider_with_value(row5_fill, "Fill Blue:", 'fill_color_blue', 0.0, 1.0, 
                                    default_values['fill_color_blue'], chart, self.update_fill_color)
        
        # Цвета заголовка
        row6 = ttk.Frame(scrollable_frame)
        row6.pack(fill=tk.X, pady=5)
        ttk.Label(row6, text="Title Colors:", font=("Arial", 10, "bold")).pack()
        row6_title = ttk.Frame(scrollable_frame)
        row6_title.pack(fill=tk.X, pady=2)
        self.create_slider_with_value(row6_title, "Title Red:", 'title_color_red', 0.0, 1.0, 
                                    default_values['title_color_red'], chart, self.update_title_color)
        self.create_slider_with_value(row6_title, "Title Green:", 'title_color_green', 0.0, 1.0, 
                                    default_values['title_color_green'], chart, self.update_title_color)
        self.create_slider_with_value(row6_title, "Title Blue:", 'title_color_blue', 0.0, 1.0, 
                                    default_values['title_color_blue'], chart, self.update_title_color)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
    
    def create_size_sliders(self, parent_frame, chart, default_values):
        """Создание ползунков размеров элементов"""
        canvas = tk.Canvas(parent_frame)
        scrollbar = ttk.Scrollbar(parent_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Основные размеры
        row1 = ttk.Frame(scrollable_frame)
        row1.pack(fill=tk.X, pady=5)
        ttk.Label(row1, text="Chart Sizes:", font=("Arial", 10, "bold")).pack()
        row1_sizes = ttk.Frame(scrollable_frame)
        row1_sizes.pack(fill=tk.X, pady=2)
        self.create_slider_with_value(row1_sizes, "Figure Width:", 'figure_width', 2.0, 12.0, 
                                    default_values['figure_width'], chart, self.update_figure_width)
        self.create_slider_with_value(row1_sizes, "Figure Height:", 'figure_height', 2.0, 8.0, 
                                    default_values['figure_height'], chart, self.update_figure_height)
        self.create_slider_with_value(row1_sizes, "Chart Area Width:", 'chart_area_width', 0.5, 1.0, 
                                    default_values['chart_area_width'], chart, self.update_chart_area_width)
        
        # Размеры элементов
        row2 = ttk.Frame(scrollable_frame)
        row2.pack(fill=tk.X, pady=5)
        ttk.Label(row2, text="Element Sizes:", font=("Arial", 10, "bold")).pack()
        row2_elements = ttk.Frame(scrollable_frame)
        row2_elements.pack(fill=tk.X, pady=2)
        # self.create_slider_with_value(row2_elements, "Legend Box Width:", 'legend_box_width', 0.1, 0.3, 
        #                             default_values['legend_box_width'], chart, self.update_legend_box_width)  # УДАЛЕНО
        self.create_slider_with_value(row2_elements, "Title Box Width:", 'title_box_width', 0.5, 1.0, 
                                    default_values['title_box_width'], chart, self.update_title_box_width)
        self.create_slider_with_value(row2_elements, "Marker Box Size:", 'marker_box_size', 0.01, 0.05, 
                                    default_values['marker_box_size'], chart, self.update_marker_box_size)
        
        # Размеры сетки
        row3 = ttk.Frame(scrollable_frame)
        row3.pack(fill=tk.X, pady=5)
        ttk.Label(row3, text="Grid Sizes:", font=("Arial", 10, "bold")).pack()
        row3_grid = ttk.Frame(scrollable_frame)
        row3_grid.pack(fill=tk.X, pady=2)
        self.create_slider_with_value(row3_grid, "Grid Cell Width:", 'grid_cell_width', 0.05, 0.2, 
                                    default_values['grid_cell_width'], chart, self.update_grid_cell_width)
        self.create_slider_with_value(row3_grid, "Grid Cell Height:", 'grid_cell_height', 0.05, 0.2, 
                                    default_values['grid_cell_height'], chart, self.update_grid_cell_height)
        self.create_slider_with_value(row3_grid, "Grid Major Width:", 'grid_major_width', 0.5, 1.0, 
                                    default_values['grid_major_width'], chart, self.update_grid_major_width)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
    
    def create_panel_sliders(self, parent_frame, chart, default_values):
        """Создание ползунков размеров панелей"""
        canvas = tk.Canvas(parent_frame)
        scrollbar = ttk.Scrollbar(parent_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Основные панели
        row1 = ttk.Frame(scrollable_frame)
        row1.pack(fill=tk.X, pady=5)
        ttk.Label(row1, text="Main Panels:", font=("Arial", 10, "bold")).pack()
        row1_panels = ttk.Frame(scrollable_frame)
        row1_panels.pack(fill=tk.X, pady=2)
        self.create_slider_with_value(row1_panels, "Side Panel Width:", 'side_panel_width', 0.1, 0.4, 
                                    default_values['side_panel_width'], chart, self.update_side_panel_width)
        self.create_slider_with_value(row1_panels, "Bottom Panel Height:", 'bottom_panel_height', 0.05, 0.3, 
                                    default_values['bottom_panel_height'], chart, self.update_bottom_panel_height)
        self.create_slider_with_value(row1_panels, "Top Panel Height:", 'top_panel_height', 0.05, 0.2, 
                                    default_values['top_panel_height'], chart, self.update_top_panel_height)
        
        # Торговые панели
        row2 = ttk.Frame(scrollable_frame)
        row2.pack(fill=tk.X, pady=5)
        ttk.Label(row2, text="Trading Panels:", font=("Arial", 10, "bold")).pack()
        row2_trading = ttk.Frame(scrollable_frame)
        row2_trading.pack(fill=tk.X, pady=2)
        self.create_slider_with_value(row2_trading, "Trading Panel Width:", 'trading_panel_width', 0.2, 0.4, 
                                    default_values['trading_panel_width'], chart, self.update_trading_panel_width)
        self.create_slider_with_value(row2_trading, "Portfolio Panel Width:", 'portfolio_panel_width', 0.25, 0.45, 
                                    default_values['portfolio_panel_width'], chart, self.update_portfolio_panel_width)
        self.create_slider_with_value(row2_trading, "Orderbook Panel Width:", 'orderbook_panel_width', 0.25, 0.5, 
                                    default_values['orderbook_panel_width'], chart, self.update_orderbook_panel_width)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
    
    def create_window_sliders(self, parent_frame, chart, default_values):
        """Создание ползунков размеров окон"""
        canvas = tk.Canvas(parent_frame)
        scrollbar = ttk.Scrollbar(parent_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Основные окна
        row1 = ttk.Frame(scrollable_frame)
        row1.pack(fill=tk.X, pady=5)
        ttk.Label(row1, text="Main Windows:", font=("Arial", 10, "bold")).pack()
        row1_windows = ttk.Frame(scrollable_frame)
        row1_windows.pack(fill=tk.X, pady=2)
        self.create_slider_with_value(row1_windows, "Main Window Width:", 'main_window_width', 0.5, 1.0, 
                                    default_values['main_window_width'], chart, self.update_main_window_width)
        self.create_slider_with_value(row1_windows, "Chart Window Width:", 'chart_window_width', 0.5, 1.0, 
                                    default_values['chart_window_width'], chart, self.update_chart_window_width)
        self.create_slider_with_value(row1_windows, "Control Panel Width:", 'control_panel_width', 0.15, 0.4, 
                                    default_values['control_panel_width'], chart, self.update_control_panel_width)
        
        # Диалоговые окна
        row2 = ttk.Frame(scrollable_frame)
        row2.pack(fill=tk.X, pady=5)
        ttk.Label(row2, text="Dialog Windows:", font=("Arial", 10, "bold")).pack()
        row2_dialogs = ttk.Frame(scrollable_frame)
        row2_dialogs.pack(fill=tk.X, pady=2)
        self.create_slider_with_value(row2_dialogs, "Dialog Width:", 'dialog_width', 0.2, 0.6, 
                                    default_values['dialog_width'], chart, self.update_dialog_width)
        self.create_slider_with_value(row2_dialogs, "Modal Width:", 'modal_width', 0.3, 0.7, 
                                    default_values['modal_width'], chart, self.update_modal_width)
        self.create_slider_with_value(row2_dialogs, "Popup Width:", 'popup_width', 0.2, 0.5, 
                                    default_values['popup_width'], chart, self.update_popup_width)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
    
    def create_message_sliders(self, parent_frame, chart, default_values):
        """Создание ползунков размеров сообщений"""
        canvas = tk.Canvas(parent_frame)
        scrollbar = ttk.Scrollbar(parent_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Уведомления
        row1 = ttk.Frame(scrollable_frame)
        row1.pack(fill=tk.X, pady=5)
        ttk.Label(row1, text="Notifications:", font=("Arial", 10, "bold")).pack()
        row1_notifications = ttk.Frame(scrollable_frame)
        row1_notifications.pack(fill=tk.X, pady=2)
        self.create_slider_with_value(row1_notifications, "Notification Width:", 'notification_width', 0.2, 0.4, 
                                    default_values['notification_width'], chart, self.update_notification_width)
        self.create_slider_with_value(row1_notifications, "Tooltip Width:", 'tooltip_width', 0.1, 0.3, 
                                    default_values['tooltip_width'], chart, self.update_tooltip_width)
        self.create_slider_with_value(row1_notifications, "Success Message Width:", 'success_message_width', 0.2, 0.4, 
                                    default_values['success_message_width'], chart, self.update_success_message_width)
        
        # Сообщения об ошибках
        row2 = ttk.Frame(scrollable_frame)
        row2.pack(fill=tk.X, pady=5)
        ttk.Label(row2, text="Error Messages:", font=("Arial", 10, "bold")).pack()
        row2_errors = ttk.Frame(scrollable_frame)
        row2_errors.pack(fill=tk.X, pady=2)
        self.create_slider_with_value(row2_errors, "Error Message Width:", 'error_message_width', 0.25, 0.5, 
                                    default_values['error_message_width'], chart, self.update_error_message_width)
        self.create_slider_with_value(row2_errors, "Warning Message Width:", 'warning_message_width', 0.25, 0.45, 
                                    default_values['warning_message_width'], chart, self.update_warning_message_width)
        self.create_slider_with_value(row2_errors, "Info Message Width:", 'info_message_width', 0.15, 0.35, 
                                    default_values['info_message_width'], chart, self.update_info_message_width)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
    
    def create_trading_sliders(self, parent_frame, chart, default_values):
        """Создание ползунков размеров торговых элементов"""
        canvas = tk.Canvas(parent_frame)
        scrollbar = ttk.Scrollbar(parent_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Торговые элементы
        row1 = ttk.Frame(scrollable_frame)
        row1.pack(fill=tk.X, pady=5)
        ttk.Label(row1, text="Trading Elements:", font=("Arial", 10, "bold")).pack()
        row1_trading = ttk.Frame(scrollable_frame)
        row1_trading.pack(fill=tk.X, pady=2)
        self.create_slider_with_value(row1_trading, "Spread Box Width:", 'spread_box_width', 0.08, 0.2, 
                                    default_values['spread_box_width'], chart, self.update_spread_box_width)
        self.create_slider_with_value(row1_trading, "Price Label Width:", 'price_label_width', 0.05, 0.15, 
                                    default_values['price_label_width'], chart, self.update_price_label_width)
        self.create_slider_with_value(row1_trading, "Volume Bar Width:", 'volume_bar_width', 0.5, 1.0, 
                                    default_values['volume_bar_width'], chart, self.update_volume_bar_width)
        
        # Аналитические элементы
        row2 = ttk.Frame(scrollable_frame)
        row2.pack(fill=tk.X, pady=5)
        ttk.Label(row2, text="Analysis Elements:", font=("Arial", 10, "bold")).pack()
        row2_analysis = ttk.Frame(scrollable_frame)
        row2_analysis.pack(fill=tk.X, pady=2)
        self.create_slider_with_value(row2_analysis, "Trend Line Box Width:", 'trend_line_box_width', 0.05, 0.2, 
                                    default_values['trend_line_box_width'], chart, self.update_trend_line_box_width)
        self.create_slider_with_value(row2_analysis, "Badge Width:", 'badge_width', 0.03, 0.1, 
                                    default_values['badge_width'], chart, self.update_badge_width)
        self.create_slider_with_value(row2_analysis, "Progress Bar Width:", 'progress_bar_width', 0.1, 0.4, 
                                    default_values['progress_bar_width'], chart, self.update_progress_bar_width)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
    
    def create_system_sliders(self, parent_frame, chart, default_values):
        """Создание ползунков размеров системных элементов"""
        canvas = tk.Canvas(parent_frame)
        scrollbar = ttk.Scrollbar(parent_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Системные элементы
        row1 = ttk.Frame(scrollable_frame)
        row1.pack(fill=tk.X, pady=5)
        ttk.Label(row1, text="System Elements:", font=("Arial", 10, "bold")).pack()
        row1_system = ttk.Frame(scrollable_frame)
        row1_system.pack(fill=tk.X, pady=2)
        self.create_slider_with_value(row1_system, "Menu Bar Width:", 'menu_bar_width', 0.5, 1.0, 
                                    default_values['menu_bar_width'], chart, self.update_menu_bar_width)
        self.create_slider_with_value(row1_system, "Toolbar Width:", 'toolbar_width', 0.4, 0.8, 
                                    default_values['toolbar_width'], chart, self.update_toolbar_width)
        self.create_slider_with_value(row1_system, "Status Bar Width:", 'status_bar_width', 0.5, 1.0, 
                                    default_values['status_bar_width'], chart, self.update_status_bar_width)
        
        # Кнопки управления
        row2 = ttk.Frame(scrollable_frame)
        row2.pack(fill=tk.X, pady=5)
        ttk.Label(row2, text="Control Buttons:", font=("Arial", 10, "bold")).pack()
        row2_buttons = ttk.Frame(scrollable_frame)
        row2_buttons.pack(fill=tk.X, pady=2)
        self.create_slider_with_value(row2_buttons, "Close Button Width:", 'close_button_width', 0.01, 0.05, 
                                    default_values['close_button_width'], chart, self.update_close_button_width)
        self.create_slider_with_value(row2_buttons, "Minimize Button Width:", 'minimize_button_width', 0.01, 0.05, 
                                    default_values['minimize_button_width'], chart, self.update_minimize_button_width)
        self.create_slider_with_value(row2_buttons, "Maximize Button Width:", 'maximize_button_width', 0.01, 0.05, 
                                    default_values['maximize_button_width'], chart, self.update_maximize_button_width)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
    
    def create_slider_with_value(self, parent, label_text, var_name, min_val, max_val, default_val, chart, update_func):
        """Создание ползунка с отображением значения"""
        # Создаем фрейм для ползунка
        slider_frame = ttk.Frame(parent)
        slider_frame.pack(side=tk.LEFT, padx=(0, 15))
        
        # Метка названия
        ttk.Label(slider_frame, text=label_text, font=('Arial', 8, 'bold')).pack(anchor=tk.W)
        
        # Фрейм для ползунка и значения
        control_frame = ttk.Frame(slider_frame)
        control_frame.pack(fill=tk.X)
        
        # Ползунок
        chart.slider_vars[var_name] = tk.DoubleVar(value=default_val)
        scale = ttk.Scale(control_frame, from_=min_val, to=max_val, variable=chart.slider_vars[var_name], 
                         orient=tk.HORIZONTAL, length=80, command=lambda v: self.update_slider_value(chart, var_name, update_func))
        scale.pack(side=tk.LEFT, padx=(0, 5))
        
        # Метка значения
        chart.slider_labels[var_name] = ttk.Label(control_frame, text=f"{default_val:.2f}", 
                                                 font=('Arial', 8), width=6)
        chart.slider_labels[var_name].pack(side=tk.LEFT)
    
    def update_slider_value(self, chart, var_name, update_func):
        """Обновление значения ползунка и вызов функции обновления"""
        value = chart.slider_vars[var_name].get()
        chart.slider_labels[var_name].config(text=f"{value:.2f}")
        update_func(chart)
    
    def update_line_opacity(self, chart):
        """Обновление прозрачности линий"""
        opacity = chart.slider_vars['line_opacity'].get()
        chart.line_cex.set_alpha(opacity)
        chart.line_dex.set_alpha(opacity)
        chart.fig.canvas.draw()
    
    def update_line_width(self, chart):
        """Обновление толщины линий"""
        width = chart.slider_vars['line_width'].get()
        chart.line_cex.set_linewidth(width)
        chart.line_dex.set_linewidth(width)
        chart.fig.canvas.draw()
    
    def update_marker_size(self, chart):
        """Обновление размера маркера (отключено)"""
        pass
    
    def update_fill_opacity(self, chart):
        """Обновление прозрачности заливки"""
        opacity = chart.slider_vars['fill_opacity'].get()
        if hasattr(chart, 'fill_cex') and chart.fill_cex is not None:
            chart.fill_cex.set_alpha(opacity)
        chart.fig.canvas.draw()
    
    def update_font_size(self, chart):
        """Обновление размера шрифта"""
        size = int(chart.slider_vars['font_size'].get())
        chart.ax.tick_params(labelsize=size)
        chart.spread_text.set_fontsize(size + 4)
        chart.cex_price_label.set_fontsize(size)
        chart.dex_price_label.set_fontsize(size)
        chart.fig.canvas.draw()
    
    def update_grid_alpha(self, chart):
        """Обновление яркости сетки"""
        alpha = chart.slider_vars['grid_alpha'].get()
        chart.ax.grid(True, alpha=alpha, linestyle='-', linewidth=0.5)
        chart.fig.canvas.draw()
    
    def update_animation_speed(self, chart):
        """Обновление скорости анимации"""
        speed = int(chart.slider_vars['animation_speed'].get())
        if hasattr(chart, 'ani'):
            chart.ani.event_source.interval = speed
        chart.animation_interval = speed
    
    def update_y_margin(self, chart):
        """Обновление отступов по Y"""
        margin = chart.slider_vars['y_margin'].get()
        chart.y_margin = margin
        # Применяем только если не ручное масштабирование
        if not chart.manual_zoom:
            if len(chart.cex_prices) > 0:
                all_prices = chart.cex_prices.copy()
                if len(chart.dex_prices) > 0:
                    all_prices.extend(chart.dex_prices)
                min_price = min(all_prices)
                max_price = max(all_prices)
                price_range = max_price - min_price
                if price_range > 0:
                    margin_value = price_range * margin
                    chart.ax.set_ylim(min_price - margin_value, max_price + margin_value)
        chart.fig.canvas.draw()
    
    def update_spread_brightness(self, chart):
        """Обновление яркости отображения спреда"""
        brightness = chart.slider_vars['spread_brightness'].get()
        chart.spread_brightness = brightness
        # Обновляем цвета спреда с учетом яркости
        if hasattr(chart, 'current_spread'):
            spread = chart.current_spread
            if abs(spread) >= 5.0:
                color = f'#{int(255/brightness):02x}4444'
            elif abs(spread) >= 2.0:
                color = f'#{int(255/brightness):02x}aa44'
            else:
                color = f'44{int(255/brightness):02x}44'
            chart.spread_text.set_color(color)
        chart.fig.canvas.draw()
    
    def update_title_size(self, chart):
        """Обновление размера заголовка"""
        size = int(chart.slider_vars['title_size'].get())
        chart.ax.set_title(chart.ax.get_title(), fontsize=size, fontweight='bold')
        chart.fig.canvas.draw()
    
    def update_legend_size(self, chart):
        """Обновление размера легенды - УДАЛЕНО"""
        # size = int(chart.slider_vars['legend_size'].get())
        # legend = chart.ax.get_legend()
        # if legend:
        #     for text in legend.get_texts():
        #         text.set_fontsize(size)
        # chart.fig.canvas.draw()
        pass
    
    def update_axis_label_size(self, chart):
        """Обновление размера подписей осей"""
        size = int(chart.slider_vars['axis_label_size'].get())
        chart.ax.set_xlabel(chart.ax.get_xlabel(), fontsize=size)
        chart.ax.set_ylabel(chart.ax.get_ylabel(), fontsize=size)
        chart.fig.canvas.draw()
    
    def update_tick_size(self, chart):
        """Обновление размера делений"""
        size = int(chart.slider_vars['tick_size'].get())
        chart.ax.tick_params(labelsize=size)
        chart.fig.canvas.draw()
    
    def update_x_margin(self, chart):
        """Обновление отступов по X"""
        margin = chart.slider_vars['x_margin'].get()
        chart.x_margin = margin
        if not chart.manual_zoom and len(chart.times) > 0:
            times_np = np.array(chart.times)
            if len(times_np) > 1:
                time_range = times_np[-1] - times_np[0]
                margin_value = time_range * margin
                chart.ax.set_xlim(times_np[0] - margin_value, times_np[-1] + margin_value)
        chart.fig.canvas.draw()
    
    def update_line_style_alpha(self, chart):
        """Обновление прозрачности стиля линий"""
        alpha = chart.slider_vars['line_style_alpha'].get()
        chart.line_cex.set_alpha(alpha)
        chart.line_dex.set_alpha(alpha)
        chart.fig.canvas.draw()
    
    def update_background_alpha(self, chart):
        """Обновление прозрачности фона"""
        alpha = chart.slider_vars['background_alpha'].get()
        chart.ax.set_facecolor(f'#000000{int(alpha*255):02x}')
        chart.fig.canvas.draw()
    
    def update_border_width(self, chart):
        """Обновление толщины границ"""
        width = chart.slider_vars['border_width'].get()
        for spine in chart.ax.spines.values():
            spine.set_linewidth(width)
        chart.fig.canvas.draw()
    
    def update_data_point_size(self, chart):
        """Обновление размера точек данных (отключено)"""
        pass
    
    def update_trend_line_width(self, chart):
        """Обновление толщины трендовых линий"""
        width = chart.slider_vars['trend_line_width'].get()
        chart.cex_guide.set_linewidth(width)
        chart.dex_guide.set_linewidth(width)
        chart.fig.canvas.draw()
    
    def update_volume_alpha(self, chart):
        """Обновление прозрачности объема"""
        alpha = chart.slider_vars['volume_alpha'].get()
        if hasattr(chart, 'fill_cex') and chart.fill_cex is not None:
            chart.fill_cex.set_alpha(alpha)
        chart.fig.canvas.draw()
    
    def update_cex_color(self, chart):
        """Обновление цвета CEX линии"""
        r = chart.slider_vars['cex_color_red'].get()
        g = chart.slider_vars['cex_color_green'].get()
        b = chart.slider_vars['cex_color_blue'].get()
        color = (r, g, b)
        chart.line_cex.set_color(color)
        chart.cex_guide.set_color(color)
        chart.cex_marker.set_color(color)
        chart.cex_price_label.set_color(color)
        chart.cex_badge.set_color(color)
        chart.fig.canvas.draw()
    
    def update_dex_color(self, chart):
        """Обновление цвета DEX линии"""
        r = chart.slider_vars['dex_color_red'].get()
        g = chart.slider_vars['dex_color_green'].get()
        b = chart.slider_vars['dex_color_blue'].get()
        color = (r, g, b)
        chart.line_dex.set_color(color)
        chart.dex_guide.set_color(color)
        chart.dex_marker.set_color(color)
        chart.dex_price_label.set_color(color)
        chart.dex_badge.set_color(color)
        chart.fig.canvas.draw()
    
    def update_grid_line_width(self, chart):
        """Обновление толщины линий сетки"""
        width = chart.slider_vars['grid_line_width'].get()
        chart.ax.grid(True, linewidth=width)
        chart.fig.canvas.draw()
    
    def update_grid_line_style(self, chart):
        """Обновление стиля линий сетки"""
        style_val = chart.slider_vars['grid_line_style'].get()
        styles = ['-', '--', '-.', ':', ' ']
        style = styles[int(style_val) % len(styles)]
        chart.ax.grid(True, linestyle=style)
        chart.fig.canvas.draw()
    
    def update_spine_color(self, chart):
        """Обновление цвета границ"""
        r = chart.slider_vars['spine_color_red'].get()
        g = chart.slider_vars['spine_color_green'].get()
        b = chart.slider_vars['spine_color_blue'].get()
        color = (r, g, b)
        for spine in chart.ax.spines.values():
            spine.set_color(color)
        chart.fig.canvas.draw()
    
    def update_text_color(self, chart):
        """Обновление цвета текста"""
        r = chart.slider_vars['text_color_red'].get()
        g = chart.slider_vars['text_color_green'].get()
        b = chart.slider_vars['text_color_blue'].get()
        color = (r, g, b)
        chart.ax.tick_params(colors=color)
        chart.ax.set_xlabel(chart.ax.get_xlabel(), color=color)
        chart.ax.set_ylabel(chart.ax.get_ylabel(), color=color)
        chart.fig.canvas.draw()
    
    def update_marker_edge_width(self, chart):
        """Обновление толщины границы маркера (отключено)"""
        pass
    
    def update_marker_alpha(self, chart):
        """Обновление прозрачности маркера (отключено)"""
        pass
    
    def update_fill_color(self, chart):
        """Обновление цвета заливки"""
        r = chart.slider_vars['fill_color_red'].get()
        g = chart.slider_vars['fill_color_green'].get()
        b = chart.slider_vars['fill_color_blue'].get()
        color = (r, g, b)
        if hasattr(chart, 'fill_cex') and chart.fill_cex is not None:
            chart.fill_cex.set_color(color)
        chart.fig.canvas.draw()
    
    def update_title_color(self, chart):
        """Обновление цвета заголовка"""
        r = chart.slider_vars['title_color_red'].get()
        g = chart.slider_vars['title_color_green'].get()
        b = chart.slider_vars['title_color_blue'].get()
        color = (r, g, b)
        chart.ax.set_title(chart.ax.get_title(), color=color)
        chart.fig.canvas.draw()
    
    def update_legend_alpha(self, chart):
        """Обновление прозрачности легенды - УДАЛЕНО"""
        # alpha = chart.slider_vars['legend_alpha'].get()
        # legend = chart.ax.get_legend()
        # if legend:
        #     legend.get_frame().set_alpha(alpha)
        # chart.fig.canvas.draw()
        pass
    
    def update_legend_frame_width(self, chart):
        """Обновление толщины рамки легенды - УДАЛЕНО"""
        # width = chart.slider_vars['legend_frame_width'].get()
        # legend = chart.ax.get_legend()
        # if legend:
        #     legend.get_frame().set_linewidth(width)
        # chart.fig.canvas.draw()
        pass
    
    def update_axis_ticks_length(self, chart):
        """Обновление длины делений"""
        length = chart.slider_vars['axis_ticks_length'].get()
        chart.ax.tick_params(length=length)
        chart.fig.canvas.draw()
    
    def update_axis_ticks_width(self, chart):
        """Обновление толщины делений"""
        width = chart.slider_vars['axis_ticks_width'].get()
        chart.ax.tick_params(width=width)
        chart.fig.canvas.draw()
    
    def update_axis_ticks_direction(self, chart):
        """Обновление направления делений"""
        direction = chart.slider_vars['axis_ticks_direction'].get()
        directions = ['in', 'out', 'inout']
        direction_name = directions[int(direction) % len(directions)]
        chart.ax.tick_params(direction=direction_name)
        chart.fig.canvas.draw()
    
    def update_minor_ticks_alpha(self, chart):
        """Обновление прозрачности минорных делений"""
        alpha = chart.slider_vars['minor_ticks_alpha'].get()
        chart.ax.tick_params(which='minor', alpha=alpha)
        chart.fig.canvas.draw()
    
    def update_major_ticks_alpha(self, chart):
        """Обновление прозрачности мажорных делений"""
        alpha = chart.slider_vars['major_ticks_alpha'].get()
        chart.ax.tick_params(which='major', alpha=alpha)
        chart.fig.canvas.draw()
    
    def update_tick_label_pad(self, chart):
        """Обновление отступа подписей делений"""
        pad = chart.slider_vars['tick_label_pad'].get()
        chart.ax.tick_params(pad=pad)
        chart.fig.canvas.draw()
    
    def update_axis_label_pad(self, chart):
        """Обновление отступа подписей осей"""
        pad = chart.slider_vars['axis_label_pad'].get()
        chart.ax.set_xlabel(chart.ax.get_xlabel(), labelpad=pad)
        chart.ax.set_ylabel(chart.ax.get_ylabel(), labelpad=pad)
        chart.fig.canvas.draw()
    
    def update_title_pad(self, chart):
        """Обновление отступа заголовка"""
        pad = chart.slider_vars['title_pad'].get()
        chart.ax.set_title(chart.ax.get_title(), pad=pad)
        chart.fig.canvas.draw()
    
    # === ФУНКЦИИ ОБНОВЛЕНИЯ РАЗМЕРОВ ===
    
    def update_figure_width(self, chart):
        """Обновление ширины фигуры"""
        width = chart.slider_vars['figure_width'].get()
        chart.fig.set_figwidth(width)
        chart.fig.canvas.draw()
    
    def update_figure_height(self, chart):
        """Обновление высоты фигуры"""
        height = chart.slider_vars['figure_height'].get()
        chart.fig.set_figheight(height)
        chart.fig.canvas.draw()
    
    def update_chart_area_width(self, chart):
        """Обновление ширины области графика"""
        width = chart.slider_vars['chart_area_width'].get()
        chart.ax.set_position([0.1, 0.1, width, 0.8])
        chart.fig.canvas.draw()
    
    def update_legend_box_width(self, chart):
        """Обновление ширины блока легенды - УДАЛЕНО"""
        # width = chart.slider_vars['legend_box_width'].get()
        # legend = chart.ax.get_legend()
        # if legend:
        #     legend.get_frame().set_boxstyle(f"round,pad={width}")
        # chart.fig.canvas.draw()
        pass
    
    def update_title_box_width(self, chart):
        """Обновление ширины блока заголовка"""
        width = chart.slider_vars['title_box_width'].get()
        chart.ax.set_title(chart.ax.get_title(), bbox={'boxstyle': f"round,pad={width}"})
        chart.fig.canvas.draw()
    
    def update_marker_box_size(self, chart):
        """Обновление размера блока маркера (отключено)"""
        pass
    
    def update_grid_cell_width(self, chart):
        """Обновление ширины ячейки сетки"""
        width = chart.slider_vars['grid_cell_width'].get()
        chart.ax.grid(True, linewidth=width * 10)
        chart.fig.canvas.draw()
    
    def update_grid_cell_height(self, chart):
        """Обновление высоты ячейки сетки"""
        height = chart.slider_vars['grid_cell_height'].get()
        chart.ax.grid(True, linewidth=height * 10)
        chart.fig.canvas.draw()
    
    def update_grid_major_width(self, chart):
        """Обновление ширины мажорной сетки"""
        width = chart.slider_vars['grid_major_width'].get()
        chart.ax.grid(True, linewidth=width * 2)
        chart.fig.canvas.draw()
    
    def update_side_panel_width(self, chart):
        """Обновление ширины боковой панели"""
        width = chart.slider_vars['side_panel_width'].get()
        # Применяем к позиции графика
        chart.ax.set_position([width, 0.1, 0.8-width, 0.8])
        chart.fig.canvas.draw()
    
    def update_bottom_panel_height(self, chart):
        """Обновление высоты нижней панели"""
        height = chart.slider_vars['bottom_panel_height'].get()
        chart.ax.set_position([0.1, height, 0.8, 0.8-height])
        chart.fig.canvas.draw()
    
    def update_top_panel_height(self, chart):
        """Обновление высоты верхней панели"""
        height = chart.slider_vars['top_panel_height'].get()
        chart.ax.set_position([0.1, 0.1, 0.8, 0.8-height])
        chart.fig.canvas.draw()
    
    def update_trading_panel_width(self, chart):
        """Обновление ширины торговой панели"""
        width = chart.slider_vars['trading_panel_width'].get()
        # Применяем к позиции графика
        chart.ax.set_position([0.1, 0.1, 0.8-width, 0.8])
        chart.fig.canvas.draw()
    
    def update_portfolio_panel_width(self, chart):
        """Обновление ширины панели портфеля"""
        width = chart.slider_vars['portfolio_panel_width'].get()
        chart.ax.set_position([0.1, 0.1, 0.8-width, 0.8])
        chart.fig.canvas.draw()
    
    def update_orderbook_panel_width(self, chart):
        """Обновление ширины панели ордербука"""
        width = chart.slider_vars['orderbook_panel_width'].get()
        chart.ax.set_position([0.1, 0.1, 0.8-width, 0.8])
        chart.fig.canvas.draw()
    
    def update_main_window_width(self, chart):
        """Обновление ширины главного окна"""
        width = chart.slider_vars['main_window_width'].get()
        chart.fig.set_figwidth(width * 10)
        chart.fig.canvas.draw()
    
    def update_chart_window_width(self, chart):
        """Обновление ширины окна графика"""
        width = chart.slider_vars['chart_window_width'].get()
        chart.fig.set_figwidth(width * 8)
        chart.fig.canvas.draw()
    
    def update_control_panel_width(self, chart):
        """Обновление ширины панели управления"""
        width = chart.slider_vars['control_panel_width'].get()
        chart.ax.set_position([width, 0.1, 0.8-width, 0.8])
        chart.fig.canvas.draw()
    
    def update_dialog_width(self, chart):
        """Обновление ширины диалогового окна"""
        width = chart.slider_vars['dialog_width'].get()
        chart.fig.set_figwidth(width * 6)
        chart.fig.canvas.draw()
    
    def update_modal_width(self, chart):
        """Обновление ширины модального окна"""
        width = chart.slider_vars['modal_width'].get()
        chart.fig.set_figwidth(width * 8)
        chart.fig.canvas.draw()
    
    def update_popup_width(self, chart):
        """Обновление ширины всплывающего окна"""
        width = chart.slider_vars['popup_width'].get()
        chart.fig.set_figwidth(width * 4)
        chart.fig.canvas.draw()
    
    def update_notification_width(self, chart):
        """Обновление ширины уведомления"""
        width = chart.slider_vars['notification_width'].get()
        # Применяем к размеру текста
        chart.ax.text(0.5, 0.5, '', fontsize=width * 20, transform=chart.ax.transAxes)
        chart.fig.canvas.draw()
    
    def update_tooltip_width(self, chart):
        """Обновление ширины подсказки"""
        width = chart.slider_vars['tooltip_width'].get()
        chart.ax.text(0.5, 0.5, '', fontsize=width * 15, transform=chart.ax.transAxes)
        chart.fig.canvas.draw()
    
    def update_success_message_width(self, chart):
        """Обновление ширины сообщения об успехе"""
        width = chart.slider_vars['success_message_width'].get()
        chart.ax.text(0.5, 0.5, '', fontsize=width * 18, transform=chart.ax.transAxes)
        chart.fig.canvas.draw()
    
    def update_error_message_width(self, chart):
        """Обновление ширины сообщения об ошибке"""
        width = chart.slider_vars['error_message_width'].get()
        chart.ax.text(0.5, 0.5, '', fontsize=width * 22, transform=chart.ax.transAxes)
        chart.fig.canvas.draw()
    
    def update_warning_message_width(self, chart):
        """Обновление ширины предупреждения"""
        width = chart.slider_vars['warning_message_width'].get()
        chart.ax.text(0.5, 0.5, '', fontsize=width * 20, transform=chart.ax.transAxes)
        chart.fig.canvas.draw()
    
    def update_info_message_width(self, chart):
        """Обновление ширины информационного сообщения"""
        width = chart.slider_vars['info_message_width'].get()
        chart.ax.text(0.5, 0.5, '', fontsize=width * 16, transform=chart.ax.transAxes)
        chart.fig.canvas.draw()
    
    def update_spread_box_width(self, chart):
        """Обновление ширины блока спреда"""
        width = chart.slider_vars['spread_box_width'].get()
        # Применяем к размеру текста спреда
        if hasattr(chart, 'spread_text'):
            chart.spread_text.set_fontsize(width * 100)
        chart.fig.canvas.draw()
    
    def update_price_label_width(self, chart):
        """Обновление ширины метки цены"""
        width = chart.slider_vars['price_label_width'].get()
        chart.cex_price_label.set_fontsize(width * 80)
        chart.dex_price_label.set_fontsize(width * 80)
        chart.fig.canvas.draw()
    
    def update_volume_bar_width(self, chart):
        """Обновление ширины бара объема"""
        width = chart.slider_vars['volume_bar_width'].get()
        if hasattr(chart, 'volume_bars'):
            for bar in chart.volume_bars:
                bar.set_width(width)
        chart.fig.canvas.draw()
    
    def update_trend_line_box_width(self, chart):
        """Обновление ширины блока трендовой линии"""
        width = chart.slider_vars['trend_line_box_width'].get()
        chart.cex_guide.set_linewidth(width * 5)
        chart.dex_guide.set_linewidth(width * 5)
        chart.fig.canvas.draw()
    
    def update_badge_width(self, chart):
        """Обновление ширины бейджа"""
        width = chart.slider_vars['badge_width'].get()
        # Применяем к размеру шрифта бейджей
        if hasattr(chart, 'cex_badge'):
            chart.cex_badge.set_fontsize(width * 60)
        if hasattr(chart, 'dex_badge'):
            chart.dex_badge.set_fontsize(width * 60)
        chart.fig.canvas.draw()
    
    def update_progress_bar_width(self, chart):
        """Обновление ширины прогресс-бара"""
        width = chart.slider_vars['progress_bar_width'].get()
        if hasattr(chart, 'progress_bar'):
            chart.progress_bar.set_width(width)
        chart.fig.canvas.draw()
    
    def update_menu_bar_width(self, chart):
        """Обновление ширины строки меню"""
        width = chart.slider_vars['menu_bar_width'].get()
        # Применяем к размеру заголовка
        chart.ax.set_title(chart.ax.get_title(), fontsize=width * 20)
        chart.fig.canvas.draw()
    
    def update_toolbar_width(self, chart):
        """Обновление ширины панели инструментов"""
        width = chart.slider_vars['toolbar_width'].get()
        # Применяем к размеру легенды
        legend = chart.ax.get_legend()
        if legend:
            legend.get_frame().set_linewidth(width * 3)
        chart.fig.canvas.draw()
    
    def update_status_bar_width(self, chart):
        """Обновление ширины строки состояния"""
        width = chart.slider_vars['status_bar_width'].get()
        # Применяем к размеру подписей осей
        chart.ax.set_xlabel(chart.ax.get_xlabel(), fontsize=width * 15)
        chart.ax.set_ylabel(chart.ax.get_ylabel(), fontsize=width * 15)
        chart.fig.canvas.draw()
    
    def update_close_button_width(self, chart):
        """Обновление ширины кнопки закрытия (отключено)"""
        pass
    
    def update_minimize_button_width(self, chart):
        """Обновление ширины кнопки сворачивания"""
        width = chart.slider_vars['minimize_button_width'].get()
        # Применяем к размеру делений
        chart.ax.tick_params(length=width * 100)
        chart.fig.canvas.draw()
    
    def update_maximize_button_width(self, chart):
        """Обновление ширины кнопки разворачивания"""
        width = chart.slider_vars['maximize_button_width'].get()
        # Применяем к размеру границ
        for spine in chart.ax.spines.values():
            spine.set_linewidth(width * 50)
        chart.fig.canvas.draw()
    
    def save_slider_settings(self, chart):
        """Сохранение настроек ползунков в файл"""
        try:
            settings = {}
            for var_name, var in chart.slider_vars.items():
                settings[var_name] = var.get()
            
            with open('chart_settings.json', 'w', encoding='utf-8') as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)
            
            messagebox.showinfo("Success", "Chart settings saved to chart_settings.json")
            logger.info("Chart settings saved")
        except Exception as e:
            logger.error(f"Error saving chart settings: {e}")
            messagebox.showerror("Error", f"Failed to save settings: {str(e)}")
    
    def load_slider_settings(self, chart):
        """Загрузка настроек ползунков из файла"""
        try:
            if os.path.exists('chart_settings.json'):
                with open('chart_settings.json', 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                
                for var_name, value in settings.items():
                    if var_name in chart.slider_vars:
                        chart.slider_vars[var_name].set(value)
                        chart.slider_labels[var_name].config(text=f"{value:.2f}")
                
                # Применяем все изменения
                self.update_line_opacity(chart)
                self.update_line_width(chart)
                self.update_marker_size(chart)
                self.update_fill_opacity(chart)
                self.update_font_size(chart)
                self.update_grid_alpha(chart)
                self.update_title_size(chart)
                # self.update_legend_size(chart)  # УДАЛЕНО
                self.update_y_margin(chart)
                self.update_x_margin(chart)
                self.update_axis_label_size(chart)
                self.update_tick_size(chart)
                self.update_animation_speed(chart)
                self.update_spread_brightness(chart)
                self.update_line_style_alpha(chart)
                self.update_background_alpha(chart)
                self.update_border_width(chart)
                self.update_data_point_size(chart)
                self.update_trend_line_width(chart)
                self.update_volume_alpha(chart)
                # Новые функции
                self.update_cex_color(chart)
                self.update_dex_color(chart)
                self.update_grid_line_width(chart)
                self.update_grid_line_style(chart)
                self.update_spine_color(chart)
                self.update_text_color(chart)
                self.update_marker_edge_width(chart)
                self.update_marker_alpha(chart)
                self.update_fill_color(chart)
                self.update_title_color(chart)
                # self.update_legend_alpha(chart)  # УДАЛЕНО
                # self.update_legend_frame_width(chart)  # УДАЛЕНО
                self.update_axis_ticks_length(chart)
                self.update_axis_ticks_width(chart)
                self.update_axis_ticks_direction(chart)
                self.update_minor_ticks_alpha(chart)
                self.update_major_ticks_alpha(chart)
                self.update_tick_label_pad(chart)
                self.update_axis_label_pad(chart)
                self.update_title_pad(chart)
                # Функции размеров
                self.update_figure_width(chart)
                self.update_figure_height(chart)
                self.update_chart_area_width(chart)
                # self.update_legend_box_width(chart)  # УДАЛЕНО
                self.update_title_box_width(chart)
                self.update_marker_box_size(chart)
                self.update_grid_cell_width(chart)
                self.update_grid_cell_height(chart)
                self.update_grid_major_width(chart)
                self.update_side_panel_width(chart)
                self.update_bottom_panel_height(chart)
                self.update_top_panel_height(chart)
                self.update_trading_panel_width(chart)
                self.update_portfolio_panel_width(chart)
                self.update_orderbook_panel_width(chart)
                self.update_main_window_width(chart)
                self.update_chart_window_width(chart)
                self.update_control_panel_width(chart)
                self.update_dialog_width(chart)
                self.update_modal_width(chart)
                self.update_popup_width(chart)
                self.update_notification_width(chart)
                self.update_tooltip_width(chart)
                self.update_success_message_width(chart)
                self.update_error_message_width(chart)
                self.update_warning_message_width(chart)
                self.update_info_message_width(chart)
                self.update_spread_box_width(chart)
                self.update_price_label_width(chart)
                self.update_volume_bar_width(chart)
                self.update_trend_line_box_width(chart)
                self.update_badge_width(chart)
                self.update_progress_bar_width(chart)
                self.update_menu_bar_width(chart)
                self.update_toolbar_width(chart)
                self.update_status_bar_width(chart)
                self.update_close_button_width(chart)
                self.update_minimize_button_width(chart)
                self.update_maximize_button_width(chart)
                
                messagebox.showinfo("Success", "Chart settings loaded from chart_settings.json")
                logger.info("Chart settings loaded")
            else:
                messagebox.showwarning("Warning", "No settings file found")
        except Exception as e:
            logger.error(f"Error loading chart settings: {e}")
            messagebox.showerror("Error", f"Failed to load settings: {str(e)}")
    
    def reset_all_sliders(self, chart):
        """Сброс всех ползунков к значениям по умолчанию"""
        default_values = {
            'line_opacity': 0.9,
            'line_width': 1.28125,
            'marker_size': 8.0,
            'fill_opacity': 0.2,
            'font_size': 8.0,
            'grid_alpha': 0.2,
            'title_size': 16.0,
            'legend_size': 11.0,
            'y_margin': 0.5,
            'x_margin': 0.0,
            'axis_label_size': 12.0,
            'tick_size': 10.0,
            'animation_speed': 30.0,
            'spread_brightness': 1.0,
            'line_style_alpha': 0.8,
            'background_alpha': 0.1,
            'border_width': 1.0,
            'data_point_size': 6.0,
            'trend_line_width': 2.0,
            'volume_alpha': 0.3
        }
        
        for var_name, default_val in default_values.items():
            if var_name in chart.slider_vars:
                chart.slider_vars[var_name].set(default_val)
                chart.slider_labels[var_name].config(text=f"{default_val:.2f}")
        
        # Применяем все изменения
        self.update_line_opacity(chart)
        self.update_line_width(chart)
        self.update_marker_size(chart)
        self.update_fill_opacity(chart)
        self.update_font_size(chart)
        self.update_grid_alpha(chart)
        self.update_title_size(chart)
        # self.update_legend_size(chart)  # УДАЛЕНО
        self.update_y_margin(chart)
        self.update_x_margin(chart)
        self.update_axis_label_size(chart)
        self.update_tick_size(chart)
        self.update_animation_speed(chart)
        self.update_spread_brightness(chart)
        self.update_line_style_alpha(chart)
        self.update_background_alpha(chart)
        self.update_border_width(chart)
        self.update_data_point_size(chart)
        self.update_trend_line_width(chart)
        self.update_volume_alpha(chart)
        # Новые функции
        self.update_cex_color(chart)
        self.update_dex_color(chart)
        self.update_grid_line_width(chart)
        self.update_grid_line_style(chart)
        self.update_spine_color(chart)
        self.update_text_color(chart)
        self.update_marker_edge_width(chart)
        self.update_marker_alpha(chart)
        self.update_fill_color(chart)
        self.update_title_color(chart)
        # self.update_legend_alpha(chart)  # УДАЛЕНО
        # self.update_legend_frame_width(chart)  # УДАЛЕНО
        self.update_axis_ticks_length(chart)
        self.update_axis_ticks_width(chart)
        self.update_axis_ticks_direction(chart)
        self.update_minor_ticks_alpha(chart)
        self.update_major_ticks_alpha(chart)
        self.update_tick_label_pad(chart)
        self.update_axis_label_pad(chart)
        self.update_title_pad(chart)
        # Функции размеров
        self.update_figure_width(chart)
        self.update_figure_height(chart)
        self.update_chart_area_width(chart)
        # self.update_legend_box_width(chart)  # УДАЛЕНО
        self.update_title_box_width(chart)
        self.update_marker_box_size(chart)
        self.update_grid_cell_width(chart)
        self.update_grid_cell_height(chart)
        self.update_grid_major_width(chart)
        self.update_side_panel_width(chart)
        self.update_bottom_panel_height(chart)
        self.update_top_panel_height(chart)
        self.update_trading_panel_width(chart)
        self.update_portfolio_panel_width(chart)
        self.update_orderbook_panel_width(chart)
        self.update_main_window_width(chart)
        self.update_chart_window_width(chart)
        self.update_control_panel_width(chart)
        self.update_dialog_width(chart)
        self.update_modal_width(chart)
        self.update_popup_width(chart)
        self.update_notification_width(chart)
        self.update_tooltip_width(chart)
        self.update_success_message_width(chart)
        self.update_error_message_width(chart)
        self.update_warning_message_width(chart)
        self.update_info_message_width(chart)
        self.update_spread_box_width(chart)
        self.update_price_label_width(chart)
        self.update_volume_bar_width(chart)
        self.update_trend_line_box_width(chart)
        self.update_badge_width(chart)
        self.update_progress_bar_width(chart)
        self.update_menu_bar_width(chart)
        self.update_toolbar_width(chart)
        self.update_status_bar_width(chart)
        self.update_close_button_width(chart)
        self.update_minimize_button_width(chart)
        self.update_maximize_button_width(chart)
    
    
    def run(self):
        """Запуск GUI"""
        self.root.mainloop()
    
    def on_closing(self):
        """Обработка закрытия окна"""
        logger.info("Closing application...")
        
        # Останавливаем фоновый мониторинг
        try:
            self.background_monitor.stop_monitoring()
            logger.info("Background monitoring stopped")
        except Exception as e:
            logger.error(f"Error stopping background monitoring: {e}")
        
        # Сохраняем черный список при закрытии
        try:
            self.background_monitor.save_blacklist()
            logger.info("Blacklist saved on exit")
        except Exception as e:
            logger.error(f"Error saving blacklist on exit: {e}")
        
        # Останавливаем все активные графики
        for chart, window in self.charts:
            try:
                logger.info(f"Stopping chart for {chart}")
                chart.stop()
                window.destroy()
            except Exception as e:
                logger.error(f"Error stopping chart: {e}")
        
        # Очищаем список графиков
        self.charts.clear()
        
        # Закрываем главное окно
        self.root.destroy()
        
        # Принудительно завершаем все потоки
        try:
            import threading
            import os
            import sys
            
            # Ждем завершения всех потоков (максимум 2 секунды)
            for thread in threading.enumerate():
                if thread != threading.current_thread():
                    thread.join(timeout=2.0)
            
            logger.info("All threads stopped")
        except Exception as e:
            logger.error(f"Error stopping threads: {e}")
        
        # Принудительное завершение процесса
        try:
            logger.info("Force terminating application...")
            os._exit(0)
        except Exception as e:
            logger.error(f"Error force terminating: {e}")
            sys.exit(0)


if __name__ == "__main__":
    try:
        # Запуск GUI приложения
        app = ChartGUI()
        app.run()
    except KeyboardInterrupt:
        logger.info("Application interrupted by user")
        print("\nApplication interrupted by user")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        print(f"Unexpected error: {e}")
    finally:
        logger.info("Application terminated")
        print("Application terminated")

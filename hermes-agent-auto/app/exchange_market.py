from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app import db

router = APIRouter(prefix='/exchange-market', tags=['Exchange Realtime Market Dashboard'])


def require_key(x_hermes_api_key: str = Header(default='')) -> None:
    settings = get_settings()
    if settings.hermes_agent_api_key and x_hermes_api_key != settings.hermes_agent_api_key:
        raise HTTPException(status_code=401, detail='Invalid API key')


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fnum(value: Any) -> Optional[float]:
    try:
        if value is None or value == '':
            return None
        return float(value)
    except Exception:
        return None


class PriceRequest(BaseModel):
    providers: List[str] = Field(default_factory=lambda: ['binance', 'okx', 'bybit', 'gate'])
    symbols: List[str] = Field(default_factory=lambda: ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'])


class CandleRequest(BaseModel):
    provider: str = 'binance'
    symbol: str = 'BTCUSDT'
    interval: str = '1m'
    limit: int = Field(default=120, ge=10, le=500)


class DepthRequest(BaseModel):
    provider: str = 'binance'
    symbol: str = 'BTCUSDT'
    limit: int = Field(default=20, ge=5, le=100)


class TradesRequest(BaseModel):
    provider: str = 'binance'
    symbol: str = 'BTCUSDT'
    limit: int = Field(default=30, ge=5, le=100)


class MatrixRequest(BaseModel):
    providers: List[str] = Field(default_factory=lambda: ['binance', 'okx', 'bybit', 'gate'])
    symbols: List[str] = Field(default_factory=lambda: ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT'])
    include_depth: bool = True
    depth_limit: int = Field(default=5, ge=5, le=20)


def normalize(provider: str, symbol: str) -> str:
    s = symbol.replace('/', '').replace('-', '').replace('_', '').upper()
    if provider == 'okx':
        if '-' in symbol:
            return symbol.upper()
        if s.endswith('USDT'):
            return s[:-4] + '-USDT'
    if provider == 'gate':
        if '_' in symbol:
            return symbol.upper()
        if s.endswith('USDT'):
            return s[:-4] + '_USDT'
    return s


def get_price(provider: str, symbol: str) -> Dict[str, Any]:
    p = provider.lower()
    s = normalize(p, symbol)
    if p == 'binance':
        data = requests.get(f'https://api.binance.com/api/v3/ticker/24hr?symbol={s}', timeout=18).json()
        if 'lastPrice' not in data:
            raise RuntimeError(str(data))
        return {'provider': p, 'symbol': s, 'price': data['lastPrice'], 'change_pct': data.get('priceChangePercent'), 'volume': data.get('volume'), 'time_utc': now()}
    if p == 'okx':
        data = requests.get(f'https://www.okx.com/api/v5/market/ticker?instId={s}', timeout=18).json()
        item = (data.get('data') or [None])[0]
        if not item:
            raise RuntimeError(str(data))
        return {'provider': p, 'symbol': s, 'price': item.get('last'), 'change_pct': None, 'volume': item.get('vol24h'), 'time_utc': now()}
    if p == 'bybit':
        data = requests.get(f'https://api.bybit.com/v5/market/tickers?category=spot&symbol={s}', timeout=18).json()
        item = ((data.get('result') or {}).get('list') or [None])[0]
        if not item:
            raise RuntimeError(str(data))
        return {'provider': p, 'symbol': s, 'price': item.get('lastPrice'), 'change_pct': item.get('price24hPcnt'), 'volume': item.get('volume24h'), 'time_utc': now()}
    if p == 'gate':
        data = requests.get(f'https://api.gateio.ws/api/v4/spot/tickers?currency_pair={s}', timeout=18).json()
        item = data[0] if isinstance(data, list) and data else None
        if not item:
            raise RuntimeError(str(data))
        return {'provider': p, 'symbol': s, 'price': item.get('last'), 'change_pct': item.get('change_percentage'), 'volume': item.get('base_volume'), 'time_utc': now()}
    raise RuntimeError(f'unsupported provider={provider}')


def get_candles(provider: str, symbol: str, interval: str, limit: int) -> Dict[str, Any]:
    p = provider.lower()
    s = normalize(p, symbol)
    if p == 'binance':
        raw = requests.get(f'https://api.binance.com/api/v3/klines?symbol={s}&interval={interval}&limit={limit}', timeout=20).json()
        candles = [{'t': int(x[0]), 'open': x[1], 'high': x[2], 'low': x[3], 'close': x[4], 'volume': x[5]} for x in raw]
    elif p == 'okx':
        bar = {'1m': '1m', '5m': '5m', '15m': '15m', '1h': '1H', '4h': '4H', '1d': '1D'}.get(interval, interval)
        data = requests.get(f'https://www.okx.com/api/v5/market/candles?instId={s}&bar={bar}&limit={limit}', timeout=20).json()
        candles = [{'t': int(x[0]), 'open': x[1], 'high': x[2], 'low': x[3], 'close': x[4], 'volume': x[5]} for x in reversed(data.get('data') or [])]
    elif p == 'bybit':
        iv = {'1m': '1', '5m': '5', '15m': '15', '1h': '60', '4h': '240', '1d': 'D'}.get(interval, interval)
        data = requests.get(f'https://api.bybit.com/v5/market/kline?category=spot&symbol={s}&interval={iv}&limit={limit}', timeout=20).json()
        candles = [{'t': int(x[0]), 'open': x[1], 'high': x[2], 'low': x[3], 'close': x[4], 'volume': x[5]} for x in reversed(((data.get('result') or {}).get('list') or []))]
    elif p == 'gate':
        data = requests.get(f'https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair={s}&interval={interval}&limit={limit}', timeout=20).json()
        candles = [{'t': int(float(x[0])) * 1000, 'volume': x[1], 'close': x[2], 'high': x[3], 'low': x[4], 'open': x[5]} for x in data]
    else:
        raise RuntimeError(f'unsupported provider={provider}')
    return {'provider': p, 'symbol': s, 'interval': interval, 'limit': limit, 'candles': candles, 'time_utc': now()}


def get_depth(provider: str, symbol: str, limit: int) -> Dict[str, Any]:
    p = provider.lower()
    s = normalize(p, symbol)
    if p == 'binance':
        data = requests.get(f'https://api.binance.com/api/v3/depth?symbol={s}&limit={limit}', timeout=18).json()
        bids, asks = data.get('bids') or [], data.get('asks') or []
    elif p == 'okx':
        data = requests.get(f'https://www.okx.com/api/v5/market/books?instId={s}&sz={limit}', timeout=18).json()
        item = (data.get('data') or [{}])[0]
        bids, asks = item.get('bids') or [], item.get('asks') or []
    elif p == 'bybit':
        data = requests.get(f'https://api.bybit.com/v5/market/orderbook?category=spot&symbol={s}&limit={limit}', timeout=18).json()
        item = data.get('result') or {}
        bids, asks = item.get('b') or [], item.get('a') or []
    elif p == 'gate':
        data = requests.get(f'https://api.gateio.ws/api/v4/spot/order_book?currency_pair={s}&limit={limit}', timeout=18).json()
        bids, asks = data.get('bids') or [], data.get('asks') or []
    else:
        raise RuntimeError(f'unsupported provider={provider}')
    return {'provider': p, 'symbol': s, 'bids': bids[:limit], 'asks': asks[:limit], 'time_utc': now()}


def get_trades(provider: str, symbol: str, limit: int) -> Dict[str, Any]:
    p = provider.lower()
    s = normalize(p, symbol)
    trades: List[Dict[str, Any]] = []
    if p == 'binance':
        data = requests.get(f'https://api.binance.com/api/v3/trades?symbol={s}&limit={limit}', timeout=18).json()
        trades = [{'price': x.get('price'), 'qty': x.get('qty'), 'side': 'sell' if x.get('isBuyerMaker') else 'buy', 'time': x.get('time')} for x in data]
    elif p == 'okx':
        data = requests.get(f'https://www.okx.com/api/v5/market/trades?instId={s}&limit={limit}', timeout=18).json()
        trades = [{'price': x.get('px'), 'qty': x.get('sz'), 'side': x.get('side'), 'time': x.get('ts')} for x in (data.get('data') or [])]
    elif p == 'bybit':
        data = requests.get(f'https://api.bybit.com/v5/market/recent-trade?category=spot&symbol={s}&limit={limit}', timeout=18).json()
        trades = [{'price': x.get('price'), 'qty': x.get('size'), 'side': x.get('side'), 'time': x.get('time')} for x in ((data.get('result') or {}).get('list') or [])]
    elif p == 'gate':
        data = requests.get(f'https://api.gateio.ws/api/v4/spot/trades?currency_pair={s}&limit={limit}', timeout=18).json()
        trades = [{'price': x.get('price'), 'qty': x.get('amount'), 'side': x.get('side'), 'time': x.get('create_time_ms')} for x in data]
    else:
        raise RuntimeError(f'unsupported provider={provider}')
    return {'provider': p, 'symbol': s, 'trades': trades[:limit], 'time_utc': now()}


def build_matrix(req: MatrixRequest) -> Dict[str, Any]:
    cells: List[Dict[str, Any]] = []
    opportunities: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    by_symbol: Dict[str, List[Dict[str, Any]]] = {s: [] for s in req.symbols}

    for symbol in req.symbols:
        for provider in req.providers:
            try:
                price = get_price(provider, symbol)
                depth = get_depth(provider, symbol, req.depth_limit) if req.include_depth else {'bids': [], 'asks': []}
                bid = fnum((depth.get('bids') or [[None]])[0][0])
                ask = fnum((depth.get('asks') or [[None]])[0][0])
                last = fnum(price.get('price'))
                spread_pct = ((ask - bid) / last * 100) if bid and ask and last else None
                cell = {
                    'provider': provider,
                    'symbol': symbol,
                    'venue_symbol': price.get('symbol'),
                    'last': last,
                    'change_pct': fnum(price.get('change_pct')),
                    'volume': fnum(price.get('volume')),
                    'best_bid': bid,
                    'best_ask': ask,
                    'local_spread_pct': round(spread_pct, 6) if spread_pct is not None else None,
                    'time_utc': price.get('time_utc'),
                }
                cells.append(cell)
                by_symbol[symbol].append(cell)
            except Exception as exc:
                errors.append({'provider': provider, 'symbol': symbol, 'error': str(exc)})

    for symbol, items in by_symbol.items():
        priced = [x for x in items if x.get('last') is not None]
        if len(priced) < 2:
            continue
        low = min(priced, key=lambda x: x['last'])
        high = max(priced, key=lambda x: x['last'])
        mid = (low['last'] + high['last']) / 2 if low['last'] and high['last'] else None
        gap = high['last'] - low['last']
        gap_pct = (gap / mid * 100) if mid else None
        opportunities.append({
            'symbol': symbol,
            'buy_reference': low['provider'],
            'sell_reference': high['provider'],
            'low_price': low['last'],
            'high_price': high['last'],
            'gap': round(gap, 8),
            'gap_pct': round(gap_pct, 6) if gap_pct is not None else None,
            'note': 'Read-only cross-exchange spread watch. Fees, slippage, funding, transfer delay and account limits are not included.',
        })

    opportunities.sort(key=lambda x: x.get('gap_pct') or 0, reverse=True)
    return {'status': 'success' if not errors else 'partial', 'cells': cells, 'opportunities': opportunities, 'errors': errors, 'time_utc': now()}


@router.get('/providers')
def providers() -> Dict[str, Any]:
    return {
        'status': 'ok',
        'providers': ['binance', 'okx', 'bybit', 'gate'],
        'symbols': ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT'],
        'channels': ['ticker', 'trades', 'depth', 'candles', 'matrix'],
        'mode': 'rest_snapshot_plus_browser_websocket',
    }


@router.post('/prices', dependencies=[Depends(require_key)])
def prices(req: PriceRequest) -> Dict[str, Any]:
    results = []
    errors = []
    for provider in req.providers:
        for symbol in req.symbols:
            try:
                results.append(get_price(provider, symbol))
            except Exception as exc:
                errors.append({'provider': provider, 'symbol': symbol, 'error': str(exc)})
    db.audit('exchange_market_prices', 'exchange_market', 'multi', {'providers': req.providers, 'symbols': req.symbols, 'errors': len(errors)}, 'success' if not errors else 'partial', 'low', 'not_required')
    return {'status': 'success' if not errors else 'partial', 'results': results, 'errors': errors, 'time_utc': now()}


@router.post('/candles', dependencies=[Depends(require_key)])
def candles(req: CandleRequest) -> Dict[str, Any]:
    try:
        result = get_candles(req.provider, req.symbol, req.interval, req.limit)
        db.audit('exchange_market_candles', 'exchange_market', f'{req.provider}:{req.symbol}', req.model_dump(), 'success', 'low', 'not_required')
        return {'status': 'success', **result}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post('/depth', dependencies=[Depends(require_key)])
def depth(req: DepthRequest) -> Dict[str, Any]:
    try:
        result = get_depth(req.provider, req.symbol, req.limit)
        db.audit('exchange_market_depth', 'exchange_market', f'{req.provider}:{req.symbol}', req.model_dump(), 'success', 'low', 'not_required')
        return {'status': 'success', **result}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post('/trades', dependencies=[Depends(require_key)])
def trades(req: TradesRequest) -> Dict[str, Any]:
    try:
        result = get_trades(req.provider, req.symbol, req.limit)
        db.audit('exchange_market_trades', 'exchange_market', f'{req.provider}:{req.symbol}', req.model_dump(), 'success', 'low', 'not_required')
        return {'status': 'success', **result}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post('/matrix', dependencies=[Depends(require_key)])
def matrix(req: MatrixRequest) -> Dict[str, Any]:
    try:
        result = build_matrix(req)
        db.audit('exchange_market_matrix', 'exchange_market', 'matrix', {'providers': req.providers, 'symbols': req.symbols, 'errors': len(result.get('errors') or [])}, result['status'], 'low', 'not_required')
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

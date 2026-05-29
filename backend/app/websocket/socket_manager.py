"""
Socket.IO Manager for Real-time Communication
"""

import logging
import socketio
from app.config import settings

logger = logging.getLogger(__name__)

# Create Socket.IO instance
sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins=settings.SOCKETIO_CORS_ALLOWED_ORIGINS,
    ping_interval=10,
    ping_timeout=5,
    logger=True,
    engineio_logger=True
)

# Store connected clients
connected_clients = {}


@sio.event
async def connect(sid, environ):
    """Handle client connection"""
    logger.info(f"Client connected: {sid}")
    connected_clients[sid] = {
        'id': sid,
        'subscriptions': set()
    }
    await sio.emit('connection_response', {
        'data': 'Connected to NeuradeX',
        'sid': sid
    }, to=sid)


@sio.event
async def disconnect(sid):
    """Handle client disconnection"""
    logger.info(f"Client disconnected: {sid}")
    if sid in connected_clients:
        del connected_clients[sid]


@sio.event
async def subscribe_stock(sid, data):
    """Subscribe to stock updates"""
    symbol = data.get('symbol')
    logger.info(f"Client {sid} subscribed to {symbol}")
    
    if sid in connected_clients:
        connected_clients[sid]['subscriptions'].add(symbol)
    
    await sio.emit('subscription_confirmed', {
        'symbol': symbol,
        'status': 'subscribed'
    }, to=sid)


@sio.event
async def unsubscribe_stock(sid, data):
    """Unsubscribe from stock updates"""
    symbol = data.get('symbol')
    logger.info(f"Client {sid} unsubscribed from {symbol}")
    
    if sid in connected_clients:
        connected_clients[sid]['subscriptions'].discard(symbol)
    
    await sio.emit('subscription_confirmed', {
        'symbol': symbol,
        'status': 'unsubscribed'
    }, to=sid)


async def emit_stock_update(symbol: str, data: dict):
    """Emit stock update to all subscribed clients"""
    for sid, client_info in connected_clients.items():
        if symbol in client_info['subscriptions']:
            await sio.emit('stock_update', {
                'symbol': symbol,
                'data': data
            }, to=sid)


async def emit_prediction_update(symbol: str, data: dict):
    """Emit prediction update to all subscribed clients"""
    for sid, client_info in connected_clients.items():
        if symbol in client_info['subscriptions']:
            await sio.emit('prediction_update', {
                'symbol': symbol,
                'prediction': data
            }, to=sid)


async def broadcast_message(event: str, data: dict):
    """Broadcast message to all connected clients"""
    await sio.emit(event, data)

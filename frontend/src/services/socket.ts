import io, { Socket } from 'socket.io-client';

const SOCKET_URL = import.meta.env.VITE_SOCKET_URL || window.location.origin;

class SocketService {
  private socket: Socket | null = null;

  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      try {
        this.socket = io(SOCKET_URL, {
          reconnection: true,
          reconnectionDelay: 1000,
          reconnectionDelayMax: 5000,
          reconnectionAttempts: 5,
        });

        this.socket.on('connect', () => {
          console.log('✅ Connected to WebSocket');
          resolve();
        });

        this.socket.on('connect_error', (error) => {
          console.error('❌ WebSocket connection error:', error);
          reject(error);
        });

        this.socket.on('disconnect', () => {
          console.log('⚠️ Disconnected from WebSocket');
        });
      } catch (error) {
        reject(error);
      }
    });
  }

  disconnect(): void {
    if (this.socket) {
      this.socket.disconnect();
    }
  }

  subscribeToStock(symbol: string): void {
    if (this.socket) {
      this.socket.emit('subscribe_stock', { symbol });
    }
  }

  unsubscribeFromStock(symbol: string): void {
    if (this.socket) {
      this.socket.emit('unsubscribe_stock', { symbol });
    }
  }

  onStockUpdate(
    callback: (data: { symbol: string; data: any }) => void
  ): void {
    if (this.socket) {
      this.socket.on('stock_update', callback);
    }
  }

  onPredictionUpdate(
    callback: (data: { symbol: string; prediction: any }) => void
  ): void {
    if (this.socket) {
      this.socket.on('prediction_update', callback);
    }
  }

  onMessage(event: string, callback: (data: any) => void): void {
    if (this.socket) {
      this.socket.on(event, callback);
    }
  }

  removeListener(event: string): void {
    if (this.socket) {
      this.socket.off(event);
    }
  }

  isConnected(): boolean {
    return this.socket?.connected || false;
  }
}

export default new SocketService();

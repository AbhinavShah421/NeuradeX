# Stock Prediction AI - Complete End-to-End System

An advanced AI-powered stock prediction system combining machine learning, deep learning, NLP, and large language models for real-time market analysis.

## 🎯 System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      Frontend (React + TypeScript)              │
│  - Real-time Dashboard with Charts & Predictions                │
│  - Portfolio Management & Alerts                                │
│  - AI-powered Chat Interface (Future)                           │
└──────────────────────────┬──────────────────────────────────────┘
                           │ WebSocket + REST API
┌──────────────────────────▼──────────────────────────────────────┐
│                    Backend (FastAPI + Python)                   │
├──────────────────────────────────────────────────────────────────┤
│  • API Routes (Stocks, Predictions, Portfolio)                 │
│  • Socket.IO Real-time Communication                           │
│  • Database Connectors (PostgreSQL, MongoDB, InfluxDB)         │
│  • ML/AI Core:                                                  │
│    - LSTM & Transformer Models for Time-Series                 │
│    - XGBoost for Ensemble Predictions                          │
│    - BERT-based Sentiment Analysis                             │
│    - Ollama (Open-source LLaMA) for Contextual Analysis        │
│  • Feature Engineering (TA-Lib, spaCy)                         │
│  • Continuous Learning Module                                  │
└──────────────────────────┬──────────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
┌───────▼──────┐  ┌────────▼────────┐  ┌─────▼────────┐
│  PostgreSQL  │  │    MongoDB      │  │    Redis     │
│  (Structured)│  │  (Time-Series)  │  │  (Cache)     │
└──────────────┘  └─────────────────┘  └──────────────┘
        │                  │                  │
        └──────────────────┼──────────────────┘
                           │
            ┌──────────────┼──────────────┐
            │              │              │
       ┌────▼────┐  ┌─────▼─────┐  ┌────▼─────┐
       │InfluxDB │  │ RabbitMQ  │  │ Ollama   │
       │(Metrics)│  │(Messages) │  │(LLM)     │
       └─────────┘  └───────────┘  └──────────┘
```

## 📋 Prerequisites

- **Docker** (v20.10+)
- **Docker Compose** (v2.0+)
- **Git**
- **Python** 3.11+ (for local development)
- **Node.js** 18+ (for frontend development)

## 🚀 Quick Start

### Option 1: Docker Compose (Recommended)

```bash
# Clone the repository
git clone <repository-url>
cd stock-prediction-ai

# Build and start all services
docker-compose up -d

# Initial setup - pull LLaMA model (optional)
docker exec stock-prediction-ollama ollama pull llama2
```

**Services will be available at:**
- 🌐 Frontend: http://localhost:3000
- 🔌 Backend API: http://localhost:8000
- 📊 API Docs: http://localhost:8000/docs
- 🐰 RabbitMQ Console: http://localhost:15672
- 📈 InfluxDB: http://localhost:8086
- 🦙 Ollama: http://localhost:11434

### Option 2: Local Development Setup

#### Backend Setup

```bash
cd backend

# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create .env file
cp .env.example .env

# Start backend
python -m uvicorn app.main:app_sio --host 0.0.0.0 --port 8000 --reload
```

#### Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Create .env file
cp .env.example .env

# Start development server
npm run dev
```

#### Database Setup (Local)

Install and run databases locally or use Docker containers:

```bash
# Using Docker for just databases
docker-compose up postgres mongodb redis rabbitmq influxdb ollama -d
```

## 📁 Project Structure

```
stock-prediction-ai/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI application
│   │   ├── config.py               # Configuration settings
│   │   ├── api/                    # API endpoints
│   │   │   ├── stocks.py           # Stock routes
│   │   │   ├── predictions.py      # Prediction routes
│   │   │   └── portfolio.py        # Portfolio routes
│   │   ├── ml_core/                # ML/AI models
│   │   │   └── initializer.py      # Model initialization
│   │   ├── database/               # Database connections
│   │   │   ├── postgres.py         # PostgreSQL setup
│   │   │   ├── mongodb.py          # MongoDB setup
│   │   │   └── __init__.py
│   │   ├── utils/                  # Utilities
│   │   │   └── redis_client.py     # Redis wrapper
│   │   ├── websocket/              # Real-time communication
│   │   │   └── socket_manager.py   # Socket.IO setup
│   │   └── services/               # Business logic
│   ├── requirements.txt            # Python dependencies
│   ├── .env                        # Environment variables
│   └── Dockerfile                  # Docker image
├── frontend/
│   ├── src/
│   │   ├── main.tsx                # React entry point
│   │   ├── App.tsx                 # Main component
│   │   ├── components/             # React components
│   │   │   └── Layout.tsx          # Main layout
│   │   ├── pages/                  # Page components
│   │   │   ├── Dashboard.tsx       # Home page
│   │   │   ├── StockDetail.tsx     # Stock detail page
│   │   │   ├── Portfolio.tsx       # Portfolio page
│   │   │   └── Predictions.tsx     # Predictions page
│   │   ├── services/               # API & Socket services
│   │   │   ├── api.ts              # API client
│   │   │   └── socket.ts           # Socket.IO client
│   │   ├── stores/                 # State management (Zustand)
│   │   │   └── appStore.ts         # App state
│   │   ├── types/                  # TypeScript types
│   │   └── styles/                 # Styles (Tailwind)
│   ├── index.html
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── tailwind.config.js
│   └── Dockerfile
├── docker/
│   ├── Dockerfile.backend          # Backend Docker image
│   └── Dockerfile.frontend         # Frontend Docker image
├── config/
│   └── nginx.conf                  # Nginx configuration
├── scripts/
│   ├── setup.sh                    # Setup script
│   └── start.sh                    # Start script
├── docker-compose.yml              # Docker Compose configuration
├── .env.example                    # Example env variables
└── README.md                       # This file
```

## 🔧 Configuration

### Environment Variables

Create `.env` files in backend and frontend directories:

**backend/.env**
```env
DEBUG=True
API_HOST=0.0.0.0
API_PORT=8000

POSTGRES_HOST=postgres
POSTGRES_USER=stock_user
POSTGRES_PASSWORD=stock_password
POSTGRES_DB=stock_prediction_db

MONGODB_HOST=mongodb
MONGODB_USER=stock_admin
MONGODB_PASSWORD=stock_password

REDIS_HOST=redis
RABBITMQ_HOST=rabbitmq
LLM_API_URL=http://ollama:11434
```

**frontend/.env**
```env
VITE_API_URL=http://localhost:8000
VITE_SOCKET_URL=http://localhost:8000
```

## 🎨 API Endpoints

### Stocks
- `GET /api/stocks/` - List all stocks
- `GET /api/stocks/{symbol}` - Get stock details
- `GET /api/stocks/{symbol}/candlesticks` - Get candlestick data
- `GET /api/stocks/{symbol}/sentiment` - Get sentiment analysis

### Predictions
- `GET /api/predictions/{symbol}` - Get AI prediction
- `POST /api/predictions/{symbol}/custom-analysis` - Custom analysis
- `GET /api/predictions/{symbol}/history` - Prediction history
- `GET /api/predictions/accuracy/stats` - Model accuracy stats

### Portfolio
- `GET /api/portfolio/` - Get portfolio
- `POST /api/portfolio/add` - Add stock
- `GET /api/portfolio/performance` - Performance metrics
- `GET /api/portfolio/alerts` - Get alerts
- `POST /api/portfolio/alerts` - Create alert

### WebSocket Events
- `subscribe_stock` - Subscribe to stock updates
- `unsubscribe_stock` - Unsubscribe from updates
- `stock_update` - Real-time stock data
- `prediction_update` - Prediction updates

## 🤖 ML/AI Components

### Models Supported
- **LSTM** - Long Short-Term Memory for time-series prediction
- **Transformers** - Multi-head attention for pattern recognition
- **XGBoost** - Gradient boosting for ensemble predictions
- **BERT** - Sentiment analysis on news and social media
- **LLaMA** - Open-source LLM for contextual analysis

### Feature Engineering
- Technical Indicators (RSI, MACD, Bollinger Bands)
- Candlestick Patterns Recognition
- Sentiment Scoring (News & Social Media)
- Market Microstructure Analysis

## 🧪 Testing

```bash
# Backend tests
cd backend
pytest

# Frontend tests
cd frontend
npm test
```

## 📊 Data Flow

```
Real-time Data
    ↓
Data Ingestion (Stock Prices, News, Social Media)
    ↓
Feature Engineering (Indicators, Sentiment, Patterns)
    ↓
ML Models (LSTM, Transformer, XGBoost)
    ↓
LLM Analysis (Ollama/LLaMA for reasoning)
    ↓
Prediction Engine (Score Aggregation)
    ↓
Backend API & WebSocket
    ↓
Frontend Display (Dashboard, Charts, Alerts)
    ↓
User Actions → Feedback Loop → Model Improvement
```

## 🔐 Security Considerations

- All database credentials in environment variables
- API authentication (to be implemented)
- Rate limiting on API endpoints
- CORS protection
- SQL injection prevention via ORM
- Encrypted database connections

## 📈 Performance Optimization

- Redis caching for frequently accessed data
- Connection pooling for database
- Lazy loading for frontend components
- WebSocket for real-time updates (less overhead than polling)
- Database indexing on key columns

## 🐛 Troubleshooting

### Services won't start
```bash
# Check logs
docker-compose logs -f backend

# Reset everything
docker-compose down -v
docker-compose up -d
```

### Database connection errors
```bash
# Verify database is healthy
docker-compose ps

# Check database logs
docker-compose logs postgres
```

### Frontend can't connect to backend
- Ensure backend is running: http://localhost:8000
- Check CORS settings in backend/app/main.py
- Verify proxy settings in frontend/vite.config.ts

### LLM/Ollama not responding
```bash
# Pull the model
docker exec stock-prediction-ollama ollama pull llama2

# Test connection
curl http://localhost:11434/api/tags
```

## 🚀 Deployment

### Docker Hub
```bash
# Build images
docker build -f docker/Dockerfile.backend -t your-repo/stock-prediction-backend .
docker build -f docker/Dockerfile.frontend -t your-repo/stock-prediction-frontend .

# Push to registry
docker push your-repo/stock-prediction-backend
docker push your-repo/stock-prediction-frontend
```

### Kubernetes (Example)
```bash
kubectl apply -f k8s/
```

### AWS ECS / GCP Cloud Run
Follow respective documentation for containerized deployment

## 📝 Future Enhancements

- [ ] User authentication & authorization
- [ ] Advanced chart library (TradingView)
- [ ] Mobile app (React Native)
- [ ] Advanced NLP for earnings calls analysis
- [ ] Automated trading bot
- [ ] Options strategy recommendations
- [ ] Community features & leaderboards
- [ ] Multi-asset class support (Crypto, Forex, Commodities)

## 📚 Technology Stack Summary

| Component | Technology |
|-----------|-----------|
| Backend Framework | FastAPI (Python) |
| Real-time | Socket.IO |
| ML/DL | TensorFlow, PyTorch, Scikit-learn |
| LLM | Ollama (Open-source LLaMA) |
| Databases | PostgreSQL, MongoDB, InfluxDB |
| Cache | Redis |
| Message Queue | RabbitMQ |
| Frontend | React + TypeScript |
| State Management | Zustand |
| Styling | Tailwind CSS |
| UI Components | Custom + Plotly/Recharts |
| Build Tool | Vite |
| Containerization | Docker & Docker Compose |

## 📄 License

This project is licensed under the MIT License.

## 🤝 Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📧 Support & Contact

For issues, questions, or suggestions:
- Open an issue on GitHub
- Contact: support@stockpredictionai.com

---

**Happy Trading! 📈**

*Disclaimer: This is an educational project. Stock market predictions are not guaranteed. Always do your own research and consult with financial advisors before making investment decisions.*

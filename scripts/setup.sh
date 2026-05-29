#!/bin/bash

# Stock Prediction AI - Setup Script
# This script sets up the entire project for first-time use

set -e

echo "🚀 Stock Prediction AI - Setup Script"
echo "======================================="
echo ""

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed. Please install Docker first."
    echo "Visit: https://www.docker.com/products/docker-desktop"
    exit 1
fi

# Check if Docker Compose is installed
if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose is not installed. Please install Docker Compose first."
    exit 1
fi

echo "✅ Docker and Docker Compose are installed"
echo ""

# Create .env files if they don't exist
echo "📝 Creating environment files..."

if [ ! -f "backend/.env" ]; then
    cp backend/.env.example backend/.env 2>/dev/null || echo "Creating backend/.env"
    echo "✅ Created backend/.env"
else
    echo "⚠️  backend/.env already exists"
fi

if [ ! -f "frontend/.env" ]; then
    cp frontend/.env.example frontend/.env 2>/dev/null || echo "Creating frontend/.env"
    echo "✅ Created frontend/.env"
else
    echo "⚠️  frontend/.env already exists"
fi

echo ""
echo "🐳 Building Docker images..."
docker-compose build

echo ""
echo "🚀 Starting services..."
docker-compose up -d

echo ""
echo "⏳ Waiting for services to be healthy..."
sleep 10

# Check if services are running
echo ""
echo "📊 Service Status:"
docker-compose ps

echo ""
echo "🎉 Setup complete!"
echo ""
echo "🌐 Services are available at:"
echo "   • Frontend:     http://localhost:3000"
echo "   • Backend API:  http://localhost:8000"
echo "   • API Docs:     http://localhost:8000/docs"
echo "   • RabbitMQ:     http://localhost:15672 (guest/guest)"
echo "   • InfluxDB:     http://localhost:8086"
echo ""
echo "📝 To pull the LLaMA model, run:"
echo "   docker exec stock-prediction-ollama ollama pull llama2"
echo ""
echo "💡 To view logs, run:"
echo "   docker-compose logs -f [service_name]"
echo ""
echo "❌ To stop all services, run:"
echo "   docker-compose down"
echo ""

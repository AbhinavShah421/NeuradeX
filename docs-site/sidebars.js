// @ts-check

/** @type {import('@docusaurus/plugin-content-docs').SidebarsConfig} */
const sidebars = {
  docsSidebar: [
    {
      type: 'doc',
      id: 'intro',
      label: '🏠 Introduction',
    },
    {
      type: 'category',
      label: '🖥️ Frontend',
      collapsed: false,
      items: [
        'frontend/overview',
        'frontend/auth-flow',
        'frontend/dashboard',
        'frontend/stock-detail',
        'frontend/portfolio-predictions',
        'frontend/mutual-funds',
        'frontend/ai-engine',
        'frontend/orders-models',
        'frontend/api-reference',
      ],
    },
    {
      type: 'category',
      label: '🧠 AI Engine & Learning',
      collapsed: false,
      items: [
        'ai-engine/learning-loop',
        'ai-engine/watchlist-autopilot',
        'ai-engine/llm-provider',
        'ai-engine/live-sessions',
        'ai-engine/data-providers',
      ],
    },
    {
      type: 'category',
      label: '🏗️ Architecture',
      collapsed: false,
      items: ['architecture/data-flow', 'architecture/dependency-matrix'],
    },
    {
      type: 'category',
      label: '🔌 Backend API (Port 8000)',
      collapsed: false,
      items: [
        'api/auth',
        'api/stocks',
        'api/predictions',
        'api/portfolio',
        'api/mutual-funds',
        'api/orders',
        'api/risk',
        'api/agent',
        'api/backtest',
        'api/paper-trading',
        'api/ai-engine',
      ],
    },
    {
      type: 'category',
      label: '⚙️ Microservices',
      collapsed: false,
      items: [
        'microservices/market-data',
        'microservices/agents',
        'microservices/ensemble-engine',
        'microservices/risk-trade',
        'microservices/feedback-trainer',
        'microservices/stock-scanner',
        'microservices/sentiment-service',
      ],
    },
    {
      type: 'category',
      label: '🛠️ Infrastructure',
      collapsed: false,
      items: [
        'infrastructure/rabbitmq',
        'infrastructure/redis',
        'infrastructure/database',
        'infrastructure/inter-service-calls',
      ],
    },
  ],
};

module.exports = sidebars;

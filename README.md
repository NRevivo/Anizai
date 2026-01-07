# Anizai - AI-Powered Event Forecasting

![Anizai Hero](/.gemini/antigravity/brain/f48c6f28-e212-4482-8ba4-708d6891f640/landing_hero_1767800430305.png)

Anizai is a sophisticated AI-powered forecasting platform that provides transparent, evidence-based predictions for future events. Built with React 19, TypeScript, and modern web technologies, Anizai delivers a professional SaaS experience for users seeking data-driven insights.

## 🌟 Features

### Professional Dashboard
- **Real-time Probability Tracking**: Dynamic circular gauge showing forecast confidence
- **Multi-metric Analysis**: Confidence scores, evidence volume, and consensus indicators
- **Live Evidence Feed**: News-style timeline of events impacting predictions
- **Market Comparison**: Side-by-side analysis with prediction market consensus
- **Sentiment Analysis**: Expert vs. public sentiment trends over time

### Intelligent Chat Interface
- **AI Assistant**: Interactive chat for follow-up questions and clarifications
- **Suggested Actions**: Context-aware action recommendations
- **Evidence Exploration**: Deep dive into specific data points

### Responsive Design
- **Desktop-First**: Optimized 3-column layout for professional workstations
- **Tablet-Friendly**: Adaptive grid that maintains usability on medium screens
- **Mobile-Ready**: Touch-optimized interface with slide-out panels

## 📸 Screenshots

### Landing Page
![How It Works](/.gemini/antigravity/brain/f48c6f28-e212-4482-8ba4-708d6891f640/how_it_works_1767800440695.png)

### Plan Selection
![Plan Selection](/.gemini/antigravity/brain/f48c6f28-e212-4482-8ba4-708d6891f640/plan_selection_1767800556703.png)

### Dashboard Views

#### Desktop
![Dashboard Overview](/.gemini/antigravity/brain/f48c6f28-e212-4482-8ba4-708d6891f640/dashboard_overview_1767800579154.png)

#### Tablet
![Tablet View](/.gemini/antigravity/brain/f48c6f28-e212-4482-8ba4-708d6891f640/dashboard_tablet_1767800623812.png)

#### Mobile
![Mobile View](/.gemini/antigravity/brain/f48c6f28-e212-4482-8ba4-708d6891f640/dashboard_mobile_1767800652057.png)

## 🚀 Getting Started

### Prerequisites
- Node.js 18+ 
- npm or yarn

### Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/anizai.git
cd anizai
```

2. Install dependencies:
```bash
cd client
npm install
```

3. Start the development server:
```bash
npm run dev
```

4. Open your browser to `http://localhost:5173`

## 🛠️ Tech Stack

### Frontend
- **React 19**: Latest React with concurrent features
- **TypeScript**: Type-safe development
- **Vite**: Lightning-fast build tool
- **Tailwind CSS**: Utility-first styling
- **shadcn/ui**: High-quality component library
- **Recharts**: Data visualization

### Design System
- **Color Palette**: Teal, Blue, Purple gradients
- **Typography**: System fonts with careful hierarchy
- **Spacing**: Consistent 4px/8px grid
- **Components**: Reusable, accessible UI elements

## 📁 Project Structure

```
client/
├── src/
│   ├── components/
│   │   ├── auth/           # Authentication components
│   │   ├── cards/          # Dashboard card components
│   │   ├── landing/        # Landing page sections
│   │   ├── plans/          # Pricing components
│   │   ├── ui/             # Reusable UI components
│   │   ├── ChatPanel.tsx   # Chat interface
│   │   ├── Dashboard.tsx   # Main dashboard
│   │   └── Sidebar.tsx     # Navigation sidebar
│   ├── pages/
│   │   ├── DashboardPage.tsx
│   │   ├── LandingPage.tsx
│   │   └── PlanSelection.tsx
│   ├── data/
│   │   └── mockData.ts     # Mock data for development
│   ├── lib/
│   │   └── utils.ts        # Utility functions
│   ├── types/
│   │   └── index.ts        # TypeScript type definitions
│   └── App.tsx             # Root component
├── public/
│   ├── logo-brain.png
│   └── logo-with-text.png
└── tailwind.config.js
```

## 🎨 Design Philosophy

Anizai follows a **professional, data-driven aesthetic**:

- **Clean & Minimal**: No unnecessary decorations or flashy elements
- **Information Dense**: Maximum insight per screen space
- **Analytical Tone**: Serious tool for serious forecasting
- **Accessible**: WCAG 2.1 AA compliant color contrasts
- **Consistent**: Unified design language across all views

## 🔐 Authentication

Currently implements **mock Google OAuth** for development. Production implementation will include:
- Google OAuth 2.0
- Secure session management
- Role-based access control

## 📊 Data Flow

1. **User Input**: Question submission via "New Forecast"
2. **AI Processing**: Multi-model analysis (simulated)
3. **Evidence Gathering**: Real-time data ingestion (simulated)
4. **Probability Calculation**: Confidence-weighted synthesis
5. **Continuous Updates**: Live feed of new evidence
6. **User Interaction**: Chat-based exploration

## 🧪 Development

### Available Scripts

- `npm run dev` - Start development server
- `npm run build` - Build for production
- `npm run preview` - Preview production build
- `npm run lint` - Run ESLint

### Code Style
- ESLint with TypeScript rules
- Prettier for formatting
- Conventional Commits

## 🗺️ Roadmap

- [ ] Backend API integration
- [ ] Real AI model integration
- [ ] User authentication (Google OAuth)
- [ ] Database persistence
- [ ] Real-time WebSocket updates
- [ ] Export functionality (PDF, CSV)
- [ ] Advanced filtering and search
- [ ] Collaborative forecasting
- [ ] API for third-party integrations

## 📝 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 👥 Contributing

Contributions are welcome! Please read our contributing guidelines before submitting PRs.

## 📧 Contact

For questions or feedback, reach out to [your-email@example.com](mailto:your-email@example.com)

---

Built with ❤️ using React, TypeScript, and modern web technologies.

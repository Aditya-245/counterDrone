import { NavLink, Route, Routes } from "react-router-dom"
import { useSimStore } from "@/state/simStore"
import LibraryPage from "@/pages/LibraryPage"
import BuilderPage from "@/pages/BuilderPage"
import BriefingPage from "@/pages/BriefingPage"
import SimPage from "@/pages/SimPage"
import AARPage from "@/pages/AARPage"
import DashboardPage from "@/pages/DashboardPage"
import InstructorPage from "@/pages/InstructorPage"
import HealthPill from "@/components/HealthPill"

const NAV = [
  { to: "/", label: "Library", end: true },
  { to: "/builder", label: "Builder" },
  { to: "/dashboard", label: "Dashboard" },
  { to: "/aar", label: "AAR" },
  { to: "/instructor", label: "Instructor" },
]

export default function App(): JSX.Element {
  const theme = useSimStore((s) => s.theme)
  const setTheme = useSimStore((s) => s.setTheme)

  return (
    <div className="app">
      <header className="topbar no-print">
        <div className="brand">
          SKY<span>GYM</span>
        </div>
        <div className="tagline">decision-quality gym &middot; not a shooting game</div>
        <nav className="nav" aria-label="Primary">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => (isActive ? "active" : "")}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <HealthPill />
        <button
          type="button"
          className="btn"
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
        >
          {theme === "dark" ? "☾" : "☀"}
        </button>
      </header>

      <main className="content">
        <Routes>
          <Route path="/" element={<LibraryPage />} />
          <Route path="/builder" element={<BuilderPage />} />
          <Route path="/briefing/:scenarioId" element={<BriefingPage />} />
          <Route path="/sim/:scenarioId" element={<SimPage />} />
          <Route path="/aar" element={<AARPage />} />
          <Route path="/aar/:sessionId" element={<AARPage />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/instructor" element={<InstructorPage />} />
          <Route path="*" element={<LibraryPage />} />
        </Routes>
      </main>
    </div>
  )
}

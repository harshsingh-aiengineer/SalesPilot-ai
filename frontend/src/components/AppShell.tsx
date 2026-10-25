import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { Bot, Inbox, LayoutDashboard, LogOut, Mail, Settings, Users, Workflow } from "lucide-react";
import clsx from "clsx";
import { setToken } from "../lib/api";

const links = [
  { to: "/", label: "Overview", icon: LayoutDashboard },
  { to: "/leads", label: "Lead CRM", icon: Users },
  { to: "/inbox", label: "Gmail Inbox", icon: Inbox },
  { to: "/agent", label: "AI Agent", icon: Bot },
  { to: "/emails", label: "Email Center", icon: Mail },
  { to: "/automation", label: "Automation", icon: Workflow },
  { to: "/settings", label: "Settings", icon: Settings },
];

export function AppShell() {
  const navigate = useNavigate();
  return (
    <div className="min-h-screen bg-paper text-ink md:grid md:grid-cols-[250px_1fr]">
      <aside className="flex flex-col bg-sidebar text-stone-100 md:min-h-screen">
        <div className="px-5 py-6">
          <div className="font-display text-2xl text-white">SalesPilot</div>
          <div className="mt-1 text-xs uppercase tracking-[0.18em] text-brass">Local sales desk</div>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-3 pb-3 md:block md:space-y-1 md:px-3">
          {links.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.to === "/"}
              className={({ isActive }) =>
                clsx(
                  "flex items-center gap-3 whitespace-nowrap rounded-2xl px-3 py-2 text-sm",
                  isActive ? "bg-white/10 text-white" : "text-stone-300 hover:bg-white/5 hover:text-white",
                )
              }
            >
              <link.icon size={16} />
              {link.label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto p-4">
          <button
            className="flex w-full items-center gap-2 rounded-2xl px-3 py-2 text-sm text-stone-300 hover:bg-white/5"
            onClick={() => {
              setToken(null);
              navigate("/login");
            }}
          >
            <LogOut size={16} /> Sign out
          </button>
        </div>
      </aside>
      <main className="min-w-0 px-4 py-6 md:px-8">
        <Outlet />
      </main>
    </div>
  );
}

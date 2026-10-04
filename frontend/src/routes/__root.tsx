import { Link, Outlet, createRootRoute } from "@tanstack/react-router";
import { HeadphonesIcon } from "lucide-react";
import { ThemeToggle } from "@/components/theme-toggle";
import { Toaster } from "@/components/ui/toast";

export const Route = createRootRoute({ component: RootLayout });

const navLink = "text-muted-foreground hover:text-foreground [&.active]:text-foreground text-sm";

function RootLayout() {
  return (
    <div className="min-h-screen">
      <nav className="bg-card flex h-12 items-center gap-4 border-b px-4 sm:gap-8 sm:px-6">
        <span className="flex items-center gap-2 text-sm font-medium">
          <HeadphonesIcon className="text-primary size-4" aria-hidden />
          <span className="hidden sm:inline">Readeck Audiobook</span>
        </span>
        <Link to="/" className={navLink} activeOptions={{ exact: true, includeSearch: false }}>
          Bookmarks
        </Link>
        <Link to="/jobs" className={navLink}>
          Jobs
        </Link>
        <Link to="/settings" className={navLink}>
          Settings
        </Link>
        <div className="flex-1" />
        <ThemeToggle />
      </nav>
      <main className="mx-auto max-w-4xl px-4 py-6 sm:px-6 sm:py-8">
        <Outlet />
      </main>
      <Toaster />
    </div>
  );
}

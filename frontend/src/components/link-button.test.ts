import { createElement, type ComponentProps, type ReactElement } from "react";
import { renderToStaticMarkup, renderToString } from "react-dom/server";
import {
  Link,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { describe, expect, it } from "vitest";

import { LinkButton } from "@/components/link-button";

describe("LinkButton", () => {
  it("renders a plain anchor with the href and button styling, not a role=button", () => {
    const html = renderToStaticMarkup(
      createElement(
        LinkButton,
        {
          variant: "outline",
          size: "sm",
          render: createElement("a", { href: "/settings", className: "" }),
        },
        "Sync settings",
      ),
    );

    expect(html).toMatch(/^<a /);
    expect(html).toContain('href="/settings"');
    expect(html).toContain("Sync settings");
    expect(html).toContain('data-slot="button"');
    expect(html).not.toContain("role=");
  });

  it("keeps the download attribute of the rendered element", () => {
    const html = renderToStaticMarkup(
      createElement(LinkButton, {
        render: createElement("a", { href: "/audio/x.mp3", download: "x.mp3", className: "" }),
        children: "Download",
      }),
    );

    expect(html).toContain('href="/audio/x.mp3"');
    expect(html).toContain('download="x.mp3"');
  });

  it("forwards className, data-slot and children to a component passed as render", () => {
    // Stand-in for TanStack's <Link>: a component that spreads its props onto an <a>.
    const ForwardingLink = (props: { href: string; className?: string }) =>
      createElement("a", props);

    const html = renderToStaticMarkup(
      createElement(
        LinkButton,
        {
          variant: "outline",
          render: createElement(ForwardingLink, { href: "/jobs", className: "" }),
        },
        "Jobs",
      ),
    );

    expect(html).toMatch(/^<a /);
    expect(html).toContain('href="/jobs"');
    expect(html).toContain('data-slot="button"');
    expect(html).toContain("inline-flex");
    expect(html).toContain(">Jobs</a>");
    expect(html).not.toContain("role=");
  });

  it("keeps the rendered element's own children when LinkButton has none", () => {
    const html = renderToStaticMarkup(
      createElement(LinkButton, {
        render: createElement("a", { href: "/x", className: "" }, "Original"),
      }),
    );

    expect(html).toContain(">Original</a>");
  });

  it("works with TanStack's real <Link>: href, classes, data-slot, children and no role", async () => {
    const rootRoute = createRootRoute();
    const indexRoute = createRoute({
      getParentRoute: () => rootRoute,
      path: "/",
      component: () =>
        createElement(
          LinkButton,
          {
            variant: "outline",
            size: "sm",
            render: createElement(Link, { to: "/jobs" }) as unknown as ReactElement<
              ComponentProps<"a">
            >,
          },
          "Jobs",
        ),
    });
    const jobsRoute = createRoute({ getParentRoute: () => rootRoute, path: "/jobs" });
    const router = createRouter({
      routeTree: rootRoute.addChildren([indexRoute, jobsRoute]),
      history: createMemoryHistory({ initialEntries: ["/"] }),
    });
    await router.load();

    const html = renderToString(createElement(RouterProvider, { router }));

    expect(html).toContain('href="/jobs"');
    expect(html).toContain('data-slot="button"');
    expect(html).toContain("inline-flex");
    expect(html).toContain(">Jobs</a>");
    expect(html).not.toContain("role=");
  });
});

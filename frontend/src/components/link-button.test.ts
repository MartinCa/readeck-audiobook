import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
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
});

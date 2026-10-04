import { useEffect, useRef } from "react";
import cytoscape from "cytoscape";
import type { Core, ElementDefinition } from "cytoscape";

export interface GraphNode {
  id: string;
  label: string;
  kind: string;
}

export interface GraphEdge {
  source: string;
  target: string;
  label: string;
}

const KIND_COLORS: Record<string, string> = {
  Supplier: "#1c765f",
  PurchaseRequisition: "#d0703f",
  PurchaseOrder: "#a8552c",
  Contract: "#4d6fa8",
  Buyer: "#7a5aa6",
  Product: "#7d8b3c",
  ProductCategory: "#3f8a8a",
  BusinessUnit: "#5b6b66",
  State: "#8a8f8c",
};

export function GraphCanvas({
  nodes,
  edges,
  selected,
  onSelect,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const container = useRef<HTMLDivElement | null>(null);
  const graph = useRef<Core | null>(null);
  const select = useRef(onSelect);
  select.current = onSelect;

  useEffect(() => {
    if (!container.current) return;
    const cy = cytoscape({
      container: container.current,
      style: [
        {
          selector: "node",
          style: {
            "background-color": "data(color)",
            label: "data(label)",
            color: "#172622",
            "font-size": 10,
            "text-valign": "bottom",
            "text-margin-y": 4,
            width: 22,
            height: 22,
            "text-wrap": "ellipsis",
            "text-max-width": "110px",
          },
        },
        { selector: "node:selected", style: { "border-width": 3, "border-color": "#df805d" } },
        {
          selector: "edge",
          style: {
            width: 1.4,
            "line-color": "#b7c7bf",
            "target-arrow-color": "#b7c7bf",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            label: "data(label)",
            "font-size": 8,
            color: "#64736d",
            "text-rotation": "autorotate",
          },
        },
      ],
      wheelSensitivity: 0.2,
      minZoom: 0.2,
      maxZoom: 1.6,
    });
    cy.on("tap", "node", (event) => select.current(event.target.id()));
    graph.current = cy;
    return () => cy.destroy();
  }, []);

  useEffect(() => {
    const cy = graph.current;
    if (!cy) return;
    const elements: ElementDefinition[] = [
      ...nodes.map((node) => ({
        data: { id: node.id, label: node.label, color: KIND_COLORS[node.kind] ?? "#6f7f78" },
      })),
      ...edges.map((edge, index) => ({
        data: { id: `e${index}-${edge.source}-${edge.target}`, ...edge },
      })),
    ];
    cy.elements().remove();
    cy.add(elements);
    cy.layout({ name: "cose", animate: false, padding: 24, nodeRepulsion: () => 9000 }).run();
  }, [nodes, edges]);

  useEffect(() => {
    const cy = graph.current;
    if (!cy) return;
    cy.nodes().unselect();
    if (selected) cy.getElementById(selected).select();
  }, [selected, nodes]);

  return <div ref={container} className="graph-canvas" data-testid="graph-canvas" />;
}

export function legend() {
  return Object.entries(KIND_COLORS);
}

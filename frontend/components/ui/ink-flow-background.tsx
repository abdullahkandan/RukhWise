"use client";

import React, { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";

interface InkFlowBackgroundProps {
  className?: string;
  /**
   * Color of the particles/threads.
   * Default: Rukhwise ink (java, --color-java: #231815).
   */
  color?: string;
  /**
   * Page background color the trails fade back into.
   * Default: Rukhwise cream (--color-cream: #f5efc6). MUST match the
   * section's actual bg -- these are literal hex strings (fed straight
   * into hexToRgb below), not CSS var references, so keep them in sync
   * with app/globals.css by hand if the theme tokens ever change.
   */
  backgroundColor?: string;
  /**
   * Opacity of the fade-to-background rect (0.0 to 1.0).
   * Lower = longer, slower trails (calmer, more editorial).
   * Default: 0.035 — much lower than a typical dark-mode neon version,
   * because on a light background even faint marks read clearly.
   */
  trailOpacity?: number;
  /**
   * Number of particles. Default: 220.
   * Kept low/sparse so it reads as fine linework, not a dense tech swarm.
   */
  particleCount?: number;
  /**
   * Speed multiplier. Default: 0.45 — slow, ambient drift.
   */
  speed?: number;
  /**
   * Max opacity any single particle/trail can reach. Default: 0.15.
   * Keeps the effect subordinate to text contrast at all times.
   */
  maxParticleOpacity?: number;
}

export default function InkFlowBackground({
  className,
  color = "#231815",
  backgroundColor = "#f5efc6",
  trailOpacity = 0.035,
  particleCount = 220,
  speed = 0.45,
  maxParticleOpacity = 0.15,
}: InkFlowBackgroundProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let width = container.clientWidth;
    let height = container.clientHeight;
    let particles: Particle[] = [];
    let animationFrameId: number;
    const mouse = { x: -1000, y: -1000 };

    // Convert hex color to rgb for rgba() fade rects
    const hexToRgb = (hex: string) => {
      const h = hex.replace("#", "");
      const bigint = parseInt(h, 16);
      return { r: (bigint >> 16) & 255, g: (bigint >> 8) & 255, b: bigint & 255 };
    };
    const bg = hexToRgb(backgroundColor);

    class Particle {
      x: number;
      y: number;
      vx: number;
      vy: number;
      age: number;
      life: number;
      constructor() {
        this.x = Math.random() * width;
        this.y = Math.random() * height;
        this.vx = 0;
        this.vy = 0;
        this.age = 0;
        this.life = Math.random() * 260 + 140; // slightly longer life = slower recycling
      }
      update() {
        const angle = (Math.cos(this.x * 0.004) + Math.sin(this.y * 0.004)) * Math.PI;
        this.vx += Math.cos(angle) * 0.12 * speed;
        this.vy += Math.sin(angle) * 0.12 * speed;

        const dx = mouse.x - this.x;
        const dy = mouse.y - this.y;
        const distance = Math.sqrt(dx * dx + dy * dy);
        const interactionRadius = 140;
        if (distance < interactionRadius) {
          const force = (interactionRadius - distance) / interactionRadius;
          this.vx -= dx * force * 0.03; // gentler repulsion than a techy demo
          this.vy -= dy * force * 0.03;
        }

        this.x += this.vx;
        this.y += this.vy;
        this.vx *= 0.96;
        this.vy *= 0.96;

        this.age++;
        if (this.age > this.life) this.reset();

        if (this.x < 0) this.x = width;
        if (this.x > width) this.x = 0;
        if (this.y < 0) this.y = height;
        if (this.y > height) this.y = 0;
      }
      reset() {
        this.x = Math.random() * width;
        this.y = Math.random() * height;
        this.vx = 0;
        this.vy = 0;
        this.age = 0;
        this.life = Math.random() * 260 + 140;
      }
      draw(context: CanvasRenderingContext2D) {
        context.fillStyle = color;
        const alpha = (1 - Math.abs(this.age / this.life - 0.5) * 2) * maxParticleOpacity;
        context.globalAlpha = alpha;
        context.fillRect(this.x, this.y, 1.2, 1.2); // fine, hairline-scale dots
      }
    }

    const init = () => {
      const dpr = window.devicePixelRatio || 1;
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      ctx.scale(dpr, dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      particles = [];
      for (let i = 0; i < particleCount; i++) particles.push(new Particle());
    };

    const animate = () => {
      // Fade toward the PAGE background color (cream), not black --
      // this is the key change from a dark-mode version.
      ctx.fillStyle = `rgba(${bg.r}, ${bg.g}, ${bg.b}, ${trailOpacity})`;
      ctx.fillRect(0, 0, width, height);

      particles.forEach((p) => {
        p.update();
        p.draw(ctx);
      });
      animationFrameId = requestAnimationFrame(animate);
    };

    const handleResize = () => {
      width = container.clientWidth;
      height = container.clientHeight;
      init();
    };
    const handleMouseMove = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      mouse.x = e.clientX - rect.left;
      mouse.y = e.clientY - rect.top;
    };
    const handleMouseLeave = () => {
      mouse.x = -1000;
      mouse.y = -1000;
    };

    init();
    animate();

    window.addEventListener("resize", handleResize);
    container.addEventListener("mousemove", handleMouseMove);
    container.addEventListener("mouseleave", handleMouseLeave);

    return () => {
      window.removeEventListener("resize", handleResize);
      container.removeEventListener("mousemove", handleMouseMove);
      container.removeEventListener("mouseleave", handleMouseLeave);
      cancelAnimationFrame(animationFrameId);
    };
  }, [color, backgroundColor, trailOpacity, particleCount, speed, maxParticleOpacity]);

  return (
    <div
      ref={containerRef}
      className={cn("absolute inset-0 w-full h-full overflow-hidden pointer-events-none", className)}
    >
      <canvas ref={canvasRef} className="block w-full h-full" />
    </div>
  );
}

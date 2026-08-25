import { useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls, Grid, Line, Text, Billboard } from "@react-three/drei";
import * as THREE from "three";

const RADIUS = 5.5;
const CONVERGE_MS = 1400;
const dummy = new THREE.Object3D();
const color = new THREE.Color();
const IBM_PLEX_MONO = "https://cdn.jsdelivr.net/fontsource/fonts/ibm-plex-mono@latest/latin-500-normal.ttf";

// Fits every point (worlds + posts) into a cube of half-size RADIUS, sharing
// one scale across all three axes — an independently-stretched axis would
// distort the neighbor structure the projection is claiming to show.
function useNormalizer(points) {
  return useMemo(() => {
    if (points.length === 0) return (p) => ({ x: 0, y: 0, z: 0 });
    const axes = ["x", "y", "z"];
    const bounds = axes.map((k) => {
      const vals = points.map((p) => p[k]);
      return [Math.min(...vals), Math.max(...vals)];
    });
    const centers = bounds.map(([lo, hi]) => (lo + hi) / 2);
    const halfSpan = Math.max(...bounds.map(([lo, hi]) => (hi - lo) / 2)) || 1;
    const scale = RADIUS / halfSpan;
    return (p) => ({
      x: (p.x - centers[0]) * scale,
      y: (p.y - centers[1]) * scale,
      z: (p.z - centers[2]) * scale,
    });
  }, [points]);
}

function PostField({ posts, targets, colorByWorldId, hoveredWorldId, onHoverPost }) {
  const meshRef = useRef();
  const startRef = useRef(null);
  const raw = useMemo(() => posts.map((p) => new THREE.Vector3(p.raw.x, p.raw.y, p.raw.z)), [posts]);
  const target = useMemo(() => posts.map((p) => new THREE.Vector3(targets[p.id].x, targets[p.id].y, targets[p.id].z)), [posts, targets]);

  useEffect(() => {
    startRef.current = performance.now();
    const mesh = meshRef.current;
    if (!mesh) return;
    posts.forEach((p, i) => {
      color.set(colorByWorldId[p.world_id]);
      mesh.setColorAt(i, color);
    });
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }, [posts, colorByWorldId]);

  useFrame(() => {
    const mesh = meshRef.current;
    if (!mesh || startRef.current === null) return;
    const elapsed = performance.now() - startRef.current;
    const t = Math.min(1, elapsed / CONVERGE_MS);
    const eased = 1 - Math.pow(1 - t, 3);

    posts.forEach((p, i) => {
      dummy.position.lerpVectors(raw[i], target[i], eased);
      const dimmed = hoveredWorldId && hoveredWorldId !== p.world_id;
      const s = dimmed ? 0.55 : 1;
      dummy.scale.setScalar(s * 0.09);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
    });
    mesh.instanceMatrix.needsUpdate = true;
  });

  return (
    <instancedMesh
      ref={meshRef}
      args={[null, null, posts.length]}
      onPointerMove={(e) => {
        e.stopPropagation();
        if (e.instanceId == null) return;
        onHoverPost(posts[e.instanceId], e.clientX, e.clientY);
      }}
      onPointerOut={() => onHoverPost(null)}
    >
      <sphereGeometry args={[1, 12, 12]} />
      <meshStandardMaterial roughness={0.4} metalness={0.05} transparent opacity={0.92} />
    </instancedMesh>
  );
}

function WorldBasin({ world, anchor, color: hex, count, active, dimmed, onHover }) {
  const floor = [anchor.x, -RADIUS + 0.01, anchor.z];
  const ringRadius = 0.35 + Math.min(1.1, Math.sqrt(count) * 0.13);

  return (
    <group onPointerOver={() => onHover(world.id)} onPointerOut={() => onHover(null)}>
      <mesh position={floor} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[ringRadius - 0.03, ringRadius, 48]} />
        <meshBasicMaterial color={hex} transparent opacity={dimmed ? 0.18 : active ? 0.95 : 0.55} side={THREE.DoubleSide} />
      </mesh>
      <Line points={[floor, [anchor.x, anchor.y, anchor.z]]} color={hex} dashed dashSize={0.12} gapSize={0.1} transparent opacity={dimmed ? 0.15 : 0.5} />
      <mesh position={[anchor.x, anchor.y, anchor.z]}>
        <sphereGeometry args={[0.14, 20, 20]} />
        <meshStandardMaterial color={hex} emissive={hex} emissiveIntensity={dimmed ? 0.1 : 0.5} />
      </mesh>
      <Billboard position={[anchor.x, anchor.y + 0.32, anchor.z]}>
        <Text fontSize={0.24} color={dimmed ? "#9aa09a" : "#14171a"} font={IBM_PLEX_MONO} anchorX="center" anchorY="bottom">
          {world.name.toUpperCase()}
        </Text>
      </Billboard>
    </group>
  );
}

function Streamlines({ posts, targets, color: hex }) {
  return posts.map((p) => (
    <Line
      key={p.id}
      points={[
        [p.raw.x, p.raw.y, p.raw.z],
        [targets[p.id].x, targets[p.id].y, targets[p.id].z],
      ]}
      color={hex}
      transparent
      opacity={0.16}
      lineWidth={1}
    />
  ));
}

function AutoRotate({ controlsRef, active }) {
  useFrame(() => {
    const controls = controlsRef.current;
    if (active && controls) controls.update();
  });
  return null;
}

export default function Scene({ worlds, posts, colorByWorldId, hoveredWorldId, onHoverWorld }) {
  const [tip, setTip] = useState(null);
  const controlsRef = useRef();
  const [autoRotate, setAutoRotate] = useState(true);

  const normalize = useNormalizer(useMemo(() => [...worlds, ...posts], [worlds, posts]));

  const worldAnchors = useMemo(() => {
    const map = {};
    for (const w of worlds) map[w.id] = normalize(w);
    return map;
  }, [worlds, normalize]);

  // Render target = raw t-SNE position pulled toward its world's anchor.
  // Pull strength scales with the post's real routing similarity (normalized
  // across this dataset) — a confident route converges deeper into the
  // basin than a marginal one. This is the honest replacement for the raw
  // scatter: structure stays legible, but the world assignment reads clearly.
  const postsWithRaw = useMemo(() => posts.map((p) => ({ ...p, raw: normalize(p) })), [posts, normalize]);

  const targets = useMemo(() => {
    const sims = posts.map((p) => p.similarity ?? 0.8);
    const minSim = Math.min(...sims);
    const maxSim = Math.max(...sims);
    const span = maxSim - minSim || 1;
    const out = {};
    for (const p of postsWithRaw) {
      const anchor = worldAnchors[p.world_id];
      if (!anchor) {
        out[p.id] = p.raw;
        continue;
      }
      const norm = ((p.similarity ?? 0.8) - minSim) / span;
      const pull = 0.32 + norm * 0.55;
      out[p.id] = {
        x: p.raw.x + (anchor.x - p.raw.x) * pull,
        y: p.raw.y + (anchor.y - p.raw.y) * pull,
        z: p.raw.z + (anchor.z - p.raw.z) * pull,
      };
    }
    return out;
  }, [postsWithRaw, worldAnchors, posts]);

  const countByWorld = useMemo(() => {
    const c = {};
    for (const p of posts) c[p.world_id] = (c[p.world_id] || 0) + 1;
    return c;
  }, [posts]);

  const hoveredPosts = useMemo(
    () => (hoveredWorldId ? postsWithRaw.filter((p) => p.world_id === hoveredWorldId) : []),
    [postsWithRaw, hoveredWorldId]
  );

  return (
    <div style={{ position: "relative", background: "var(--panel)", border: "1px solid var(--border)", borderRadius: 4 }}>
      <Canvas camera={{ position: [8, 6, 9], fov: 42 }} onPointerDown={() => setAutoRotate(false)} style={{ height: 620, cursor: "grab" }}>
        <color attach="background" args={["#ffffff"]} />
        <ambientLight intensity={0.9} />
        <directionalLight position={[6, 10, 4]} intensity={0.7} />

        <Grid
          position={[0, -RADIUS, 0]}
          args={[RADIUS * 2.6, RADIUS * 2.6]}
          cellSize={0.55}
          cellThickness={0.6}
          cellColor="#e3e6e0"
          sectionSize={2.75}
          sectionThickness={1}
          sectionColor="#c7cbc2"
          fadeDistance={22}
          fadeStrength={1.2}
          infiniteGrid={false}
        />

        {worlds.map((w) => (
          <WorldBasin
            key={w.id}
            world={w}
            anchor={worldAnchors[w.id]}
            color={colorByWorldId[w.id]}
            count={countByWorld[w.id] || 0}
            active={hoveredWorldId === w.id}
            dimmed={hoveredWorldId && hoveredWorldId !== w.id}
            onHover={onHoverWorld}
          />
        ))}

        {hoveredWorldId && <Streamlines posts={hoveredPosts} targets={targets} color={colorByWorldId[hoveredWorldId]} />}

        <PostField
          posts={postsWithRaw}
          targets={targets}
          colorByWorldId={colorByWorldId}
          hoveredWorldId={hoveredWorldId}
          onHoverPost={(post, x, y) => {
            if (!post) {
              setTip(null);
              onHoverWorld(null);
              return;
            }
            onHoverWorld(post.world_id);
            setTip({
              x,
              y,
              worldName: worlds.find((w) => w.id === post.world_id)?.name ?? "unknown",
              color: colorByWorldId[post.world_id],
              caption: post.caption,
            });
          }}
        />

        <OrbitControls ref={controlsRef} autoRotate={autoRotate} autoRotateSpeed={0.6} enablePan={false} minDistance={5} maxDistance={22} makeDefault />
        <AutoRotate controlsRef={controlsRef} active={autoRotate} />
      </Canvas>

      <div className="mono" style={{ position: "absolute", left: 12, bottom: 10, fontSize: 10, color: "var(--text-dim)", letterSpacing: 1 }}>
        DRAG TO ORBIT &middot; SCROLL TO ZOOM
      </div>

      {tip && (
        <div
          className="mono"
          style={{
            position: "fixed",
            left: tip.x,
            top: tip.y,
            transform: "translate(16px, 16px)",
            maxWidth: 260,
            background: "var(--panel)",
            border: "1px solid var(--border)",
            borderRadius: 4,
            padding: "8px 10px",
            fontSize: 11,
            pointerEvents: "none",
            boxShadow: "0 6px 16px rgba(20, 23, 26, 0.08)",
          }}
        >
          <div style={{ color: tip.color, marginBottom: 4 }}>{tip.worldName}</div>
          <div style={{ color: "var(--text-muted)", fontFamily: "Space Grotesk, sans-serif" }}>{tip.caption || "(no caption)"}</div>
        </div>
      )}
    </div>
  );
}

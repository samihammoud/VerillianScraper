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

const muted = new THREE.Color();
const bright = new THREE.Color("#ffffff");

// Posts read as secondary evidence, not the headline: small and muted by
// default so the world basins carry the eye, with a single post lifting to
// full size/brightness under the cursor to stay findable on hover.
function PostField({ posts, targets, colorByWorldId, hoveredWorldId }) {
  const meshRef = useRef();
  const startRef = useRef(null);
  const raw = useMemo(() => posts.map((p) => new THREE.Vector3(p.raw.x, p.raw.y, p.raw.z)), [posts]);
  const target = useMemo(() => posts.map((p) => new THREE.Vector3(targets[p.id].x, targets[p.id].y, targets[p.id].z)), [posts, targets]);

  useEffect(() => {
    startRef.current = performance.now();
  }, [posts, colorByWorldId, hoveredWorldId]);

  // ponytail: writes instance data only while the converge tween is running (or
  // right after a hover change re-arms it), then goes idle. The old loop rewrote
  // every matrix + color on every frame forever, which is what melted the render
  // rate. Upgrade path: move the tween into a shader if it ever needs to be live.
  useFrame(() => {
    const mesh = meshRef.current;
    if (!mesh || startRef.current === null) return;
    const elapsed = performance.now() - startRef.current;
    const t = Math.min(1, elapsed / CONVERGE_MS);
    const eased = 1 - Math.pow(1 - t, 3);

    posts.forEach((p, i) => {
      dummy.position.lerpVectors(raw[i], target[i], eased);
      const dimmed = hoveredWorldId && hoveredWorldId !== p.world_id;
      dummy.scale.setScalar((dimmed ? 0.45 : 0.75) * 0.08);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);

      muted.set(colorByWorldId[p.world_id]);
      muted.lerp(bright, dimmed ? 0.6 : 0.25);
      mesh.setColorAt(i, muted);
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    if (t >= 1) startRef.current = null; // settled — stop touching buffers
  });

  return (
    <instancedMesh ref={meshRef} args={[null, null, posts.length]} raycast={() => null}>
      <sphereGeometry args={[1, 12, 12]} />
      <meshStandardMaterial roughness={0.5} metalness={0.05} transparent opacity={0.6} />
    </instancedMesh>
  );
}

function WorldBasin({ world, anchor, color: hex, count, active, dimmed, onHover }) {
  const floor = [anchor.x, -RADIUS + 0.01, anchor.z];
  const ringRadius = 0.5 + Math.min(1.35, Math.sqrt(count) * 0.16);

  return (
    <group onPointerOver={() => onHover(world.id)} onPointerOut={() => onHover(null)}>
      <mesh position={floor} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[ringRadius - 0.045, ringRadius, 48]} />
        <meshBasicMaterial color={hex} transparent opacity={dimmed ? 0.18 : active ? 1 : 0.8} side={THREE.DoubleSide} />
      </mesh>
      <Line points={[floor, [anchor.x, anchor.y, anchor.z]]} color={hex} dashed dashSize={0.12} gapSize={0.1} transparent opacity={dimmed ? 0.15 : 0.65} lineWidth={active ? 2 : 1} />
      <mesh position={[anchor.x, anchor.y, anchor.z]}>
        <sphereGeometry args={[0.21, 24, 24]} />
        <meshStandardMaterial color={hex} emissive={hex} emissiveIntensity={dimmed ? 0.12 : 0.75} />
      </mesh>
      <Billboard position={[anchor.x, anchor.y + 0.38, anchor.z]}>
        <Text
          fontSize={active ? 0.32 : 0.28}
          color={dimmed ? "#9aa09a" : "#14171a"}
          font={IBM_PLEX_MONO}
          anchorX="center"
          anchorY="bottom"
          outlineWidth={dimmed ? 0 : 0.006}
          outlineColor={hex}
        >
          {world.name.toUpperCase()}
        </Text>
      </Billboard>
    </group>
  );
}

const MAX_STREAMLINES = 120; // ponytail: one <Line> = one mesh; uncapped this was thousands of draw calls

function Streamlines({ posts, targets, color: hex }) {
  return posts.slice(0, MAX_STREAMLINES).map((p) => (
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
    <div style={{ position: "relative", flex: "1 1 auto", minWidth: 0 }}>
      <Canvas camera={{ position: [8, 6, 9], fov: 42 }} onPointerDown={() => setAutoRotate(false)} style={{ height: "100%", cursor: "grab" }}>
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

        <PostField posts={postsWithRaw} targets={targets} colorByWorldId={colorByWorldId} hoveredWorldId={hoveredWorldId} />

        <OrbitControls ref={controlsRef} autoRotate={autoRotate} autoRotateSpeed={0.6} enablePan={false} minDistance={5} maxDistance={22} makeDefault />
        <AutoRotate controlsRef={controlsRef} active={autoRotate} />
      </Canvas>

      <div className="mono" style={{ position: "absolute", left: 12, bottom: 10, fontSize: 10, color: "var(--text-dim)", letterSpacing: 1 }}>
        DRAG TO ORBIT &middot; SCROLL TO ZOOM
      </div>

      <div className="mono" style={{ position: "absolute", right: 12, bottom: 10, fontSize: 10, color: "var(--text-dim)", letterSpacing: 0.5, textAlign: "right", maxWidth: 340 }}>
        3D t-SNE, pulled toward basin by routing confidence &middot; position ≠ literal axis
      </div>

    </div>
  );
}

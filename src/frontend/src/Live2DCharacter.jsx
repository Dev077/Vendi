import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import * as PIXI from 'pixi.js';
import { Live2DModel } from 'pixi-live2d-display';

window.PIXI = PIXI;

const Live2DCharacter = forwardRef(function Live2DCharacter(
  { modelUrl, draggable = true, clickToPlayMotion = true, style },
  ref,
) {
  const containerRef = useRef(null);
  const appRef = useRef(null);
  const modelRef = useRef(null);
  const expressionParamsRef = useRef({});
  const motionGroupsRef = useRef([]);

  useImperativeHandle(ref, () => ({
    playMotion(group, index) {
      const model = modelRef.current;
      if (!model) return;
      model.motion(group, index);
    },
    playRandomMotion(group = '') {
      const model = modelRef.current;
      if (!model) return;
      const defs = model.internalModel.motionManager.definitions[group] || [];
      if (!defs.length) return;
      model.motion(group, Math.floor(Math.random() * defs.length));
    },
    setExpressionParams(params) {
      expressionParamsRef.current = { ...expressionParamsRef.current, ...params };
    },
    clearExpressionParams() {
      expressionParamsRef.current = {};
    },
    getMotionGroups() {
      return motionGroupsRef.current;
    },
  }));

  useEffect(() => {
    let cancelled = false;

    const container = containerRef.current;
    // Let PIXI manage its own <canvas>. Reusing a canvas that already had
    // a WebGL context (which happens under React StrictMode's double-invoke
    // of effects) throws inside Renderer.create.
    const app = new PIXI.Application({
      autoStart: true,
      resizeTo: container,
      backgroundAlpha: 0,
    });
    appRef.current = app;
    const view = app.view;
    view.style.display = 'block';
    view.style.width = '100%';
    view.style.height = '100%';
    container.appendChild(view);

    (async () => {
      let model;
      try {
        model = await Live2DModel.from(modelUrl);
      } catch (err) {
        console.error(`[Live2DCharacter] failed to load model from ${modelUrl}:`, err);
        return;
      }
      if (cancelled) {
        model.destroy();
        return;
      }
      modelRef.current = model;
      app.stage.addChild(model);

      const fit = () => {
        const w = app.renderer.width;
        const h = app.renderer.height;
        const scale = Math.min((w * 0.9) / model.width, (h * 0.9) / model.height);
        model.scale.set(scale);
        model.x = (w - model.width) / 2;
        model.y = (h - model.height) / 2;
      };
      fit();
      const resizeObserver = new ResizeObserver(fit);
      resizeObserver.observe(container);
      app.fit = fit;
      app.resizeObserver = resizeObserver;

      motionGroupsRef.current = Object.keys(model.internalModel.motionManager.definitions || {});

      model.on('beforeModelUpdate', () => {
        const params = expressionParamsRef.current;
        for (const id in params) {
          model.internalModel.coreModel.setParameterValueById(id, params[id]);
        }
      });

      if (draggable) {
        model.buttonMode = true;
        const onDown = (e) => {
          model.dragging = true;
          model._pointerX = e.data.global.x - model.x;
          model._pointerY = e.data.global.y - model.y;
        };
        const onMove = (e) => {
          if (model.dragging) {
            model.position.x = e.data.global.x - model._pointerX;
            model.position.y = e.data.global.y - model._pointerY;
          }
        };
        const onUp = () => { model.dragging = false; };
        model.on('pointerdown', onDown);
        model.on('pointermove', onMove);
        model.on('pointerup', onUp);
        model.on('pointerupoutside', onUp);
      }

      if (clickToPlayMotion) {
        model.on('hit', () => {
          const groups = motionGroupsRef.current;
          const group = groups[0] ?? '';
          const defs = model.internalModel.motionManager.definitions[group] || [];
          if (defs.length) model.motion(group, Math.floor(Math.random() * defs.length));
        });
      }
    })();

    return () => {
      cancelled = true;
      const model = modelRef.current;
      if (app.resizeObserver) app.resizeObserver.disconnect();
      if (model) {
        model.removeAllListeners();
        modelRef.current = null;
      }
      // `removeView: true` (second positional arg) tells PIXI to remove the
      // <canvas> from the DOM as part of destroy.
      app.destroy(true, { children: true, texture: true, baseTexture: true });
      appRef.current = null;
    };
  }, [modelUrl, draggable, clickToPlayMotion]);

  return (
    <div ref={containerRef} style={{ position: 'relative', width: '100%', height: '100%', ...style }} />
  );
});

export default Live2DCharacter;

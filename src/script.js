const localModel = "haru_greeter_pro_jp/runtime/haru_greeter_t05.model3.json";
const live2d = PIXI.live2d;

(async function main() {
  const app = new PIXI.Application({
    view: document.getElementById("canvas"),
    autoStart: true,
    resizeTo: window,
    backgroundColor: 0x000000
  });

  const model = await live2d.Live2DModel.from(localModel);
  app.stage.addChild(model);

  // Layout
  model.scale.set(Math.min((innerWidth * 0.9) / model.width, (innerHeight * 0.9) / model.height));
  model.y = (innerHeight - model.height) / 2;
  model.x = (innerWidth - model.width) / 2;

  // Global access for console debugging
  window.model = model;

  const expressionParams = [
    { id: "ParamMouthOpenY", name: "Mouth Open", min: 0, max: 1, val: 0 },
    { id: "ParamMouthForm", name: "Mouth Form", min: -1, max: 1, val: 0 },
    { id: "ParamTere", name: "Blush", min: 0, max: 1, val: 0 },
    { id: "ParamTear", name: "Tears", min: 0, max: 1, val: 0 },
    { id: "ParamEyeLOpen", name: "Eye L Open", min: 0, max: 2, val: 1 },
    { id: "ParamEyeROpen", name: "Eye R Open", min: 0, max: 2, val: 1 },
    { id: "ParamEyeLSmile", name: "Eye L Smile", min: 0, max: 1, val: 0 },
    { id: "ParamEyeRSmile", name: "Eye R Smile", min: 0, max: 1, val: 0 },
    { id: "ParamBrowLY", name: "Brow L (Y)", min: -1, max: 1, val: 0 },
    { id: "ParamBrowRY", name: "Brow R (Y)", min: -1, max: 1, val: 0 },
    { id: "ParamEyeBallX", name: "Look (X)", min: -1, max: 1, val: 0 },
    { id: "ParamEyeBallY", name: "Look (Y)", min: -1, max: 1, val: 0 }
  ];

  const controlDiv = document.getElementById("control");
  controlDiv.innerHTML = "<h3>Facial Controls</h3>";

  expressionParams.forEach(p => {
    const container = document.createElement("div");
    container.style.marginBottom = "15px";
    container.innerHTML = `
      <div style="display:flex; justify-content:space-between">
        <label style="font-size:11px; color:#aaa">${p.name}</label>
        <span id="val-${p.id}" style="font-size:10px; color:#00ffcc">${p.val}</span>
      </div>
      <input type="range" min="${p.min}" max="${p.max}" step="0.01" value="${p.val}" style="width:100%">
    `;
    const slider = container.querySelector("input");
    const display = container.querySelector("span");
    slider.oninput = () => {
      p.val = parseFloat(slider.value);
      display.innerText = p.val.toFixed(2);
    };
    controlDiv.appendChild(container);
  });

  // RESTORE DRAG & ZOOM
  draggable(model);
  setupZoom(model);

  // --- THE APPLICATION LOOP ---
  let frameCount = 0;
  
  app.ticker.add(() => {
    const core = model.internalModel.coreModel;
    
    // 1. Apply our manual slider values
    expressionParams.forEach(p => {
        core.setParameterValueById(p.id, p.val);
    });

    // 2. CONSTANT MONITORING LOG
    // Prints all 42 parameters every 60 frames (~1 second) to avoid lag
    frameCount++;
    if (frameCount % 60 === 0) {
        const allParams = {};
        core._parameterIds.forEach((id) => {
            allParams[id] = core.getParameterValueById(id).toFixed(2);
        });
        console.clear(); // Keeps console clean
        console.log("%c--- CONSTANT PARAMETER MONITOR ---", "color: #00ffcc; font-weight: bold;");
        console.table(allParams);
    }
  });

})();

function setupZoom(model) {
  const canvas = document.getElementById("canvas");
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    const zoomSpeed = 0.001;
    let newScale = model.scale.x - e.deltaY * zoomSpeed * model.scale.x;
    model.scale.set(Math.max(0.01, Math.min(5, newScale)));
  }, { passive: false });
}

function draggable(model) {
  model.buttonMode = true;
  model.on("pointerdown", e => {
    model.dragging = true;
    model._pointerX = e.data.global.x - model.x;
    model._pointerY = e.data.global.y - model.y;
  });
  model.on("pointermove", e => {
    if (model.dragging) {
      model.position.x = e.data.global.x - model._pointerX;
      model.position.y = e.data.global.y - model._pointerY;
    }
  });
  model.on("pointerupoutside", () => model.dragging = false);
  model.on("pointerup", () => model.dragging = false);
}

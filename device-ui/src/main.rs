#![cfg_attr(
    all(not(debug_assertions), target_os = "windows"),
    windows_subsystem = "windows"
)]
mod app;
mod audio;
mod backlight;
mod domain;
mod pairing_qr;
mod services;
mod tuner;

fn main() -> eframe::Result {
    let icon_image = image::load_from_memory(include_bytes!("assets/app-icon.png"))
        .expect("bundled application icon")
        .into_rgba8();
    let icon = eframe::egui::IconData {
        width: icon_image.width(),
        height: icon_image.height(),
        rgba: icon_image.into_raw(),
    };
    let windowed = std::env::args().any(|a| a == "--windowed");
    // Supplying inner_size in fullscreen makes egui-winit reapply that size
    // after creating the borderless window, shrinking it on Windows.
    let mut viewport = eframe::egui::ViewportBuilder::default()
        .with_icon(icon)
        .with_min_inner_size([800.0, 480.0])
        .with_fullscreen(!windowed);
    if windowed {
        viewport = viewport.with_inner_size([1280.0, 800.0]);
    }
    eframe::run_native(
        "ChordSense",
        eframe::NativeOptions {
            viewport,
            ..Default::default()
        },
        Box::new(|cc| Ok(Box::new(app::ChordSense::new(cc)))),
    )
}

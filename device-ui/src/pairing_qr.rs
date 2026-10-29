//! Encode the companion website locally; no third-party QR service is needed.
use eframe::egui::{self, Color32, ColorImage, TextureHandle, TextureOptions, Ui};
use qrcode::{Color, QrCode};

#[derive(Default)]
pub struct WebsiteQr {
    url: String,
    texture: Option<TextureHandle>,
}

fn website_image(url: &str) -> Option<ColorImage> {
    let parsed = reqwest::Url::parse(url).ok()?;
    if parsed.scheme() != "https" || parsed.host_str().is_none() {
        return None;
    }
    let code = QrCode::new(url.as_bytes()).ok()?;
    // Preserve a four-module white quiet zone on every edge for phone scanners.
    let side = code.width() + 8;
    let mut image = ColorImage::new([side, side], Color32::WHITE);
    for y in 0..code.width() {
        for x in 0..code.width() {
            if code[(x, y)] == Color::Dark {
                image.pixels[(y + 4) * side + x + 4] = Color32::BLACK;
            }
        }
    }
    Some(image)
}

impl WebsiteQr {
    pub fn show(&mut self, ui: &mut Ui, url: &str, max_size: f32) {
        if self.url != url {
            self.url = url.to_owned();
            self.texture = website_image(url).map(|image| {
                ui.ctx().load_texture("pairing_website_qr", image, TextureOptions::NEAREST)
            });
        }
        if let Some(texture) = &self.texture {
            let modules = texture.size()[0] as f32;
            let pixels_per_point = ui.ctx().pixels_per_point();
            let module_pixels = (max_size * pixels_per_point / modules).floor().max(1.0);
            let side = modules * module_pixels / pixels_per_point;
            ui.add(egui::Image::new(texture).fit_to_exact_size(egui::vec2(side, side)));
        } else {
            ui.label("QR code unavailable. Use the website link.");
        }
    }
}

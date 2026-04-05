/*
 * SPDX-FileCopyrightText: 2021-2025 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <string.h>
#include <stdio.h>
#include "sdkconfig.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "esp_adc/adc_continuous.h"
#include "dsps_fft2r.h"
#include "dsps_wind.h"
//#include "dsps_fft2r_platform.h"
#include <math.h>

#define ADC_UNIT                    ADC_UNIT_1
#define ADC_CONV_MODE               ADC_CONV_SINGLE_UNIT_1
#define ADC_ATTEN                   ADC_ATTEN_DB_6
#define ADC_BIT_WIDTH               SOC_ADC_DIGI_MAX_BITWIDTH

#define READ_LEN                    256 // each sample is 4 bytes long, meaning we have 64 samples per DMA write
#define FFT_SIZE                    2048 // we'll need 32 ADC frames to fill this (2040 / 64 samples per DMA write)
#define SAMPLE_RATE 20000
#define CQT_BINS    84      // 7 octaves * 12 semitones
#define FMIN        32.7f   // C1

float fft_input[FFT_SIZE * 2];   // complex
float fft_output[FFT_SIZE];
float fft_mag[FFT_SIZE / 2];
float cqt_output[CQT_BINS];

int cqt_bin_start[CQT_BINS];
int cqt_bin_end[CQT_BINS];

static adc_channel_t channel[1] = {ADC_CHANNEL_2};

float fft_buffer[FFT_SIZE];
uint16_t fft_index = 0;

static TaskHandle_t s_task_handle;
static const char *TAG = "EXAMPLE";


void print_fft_coeffs(float *fft_input, int fft_size) {
    printf("FFT Coefficients (Real + Imag):\n");
    for (int i = 0; i < fft_size; i++) {
        printf("[%4d]  Mag: %10.6f\n",
               i, fft_input[i]);
    }
    printf("END FFT\n\n");
}

void print_fft_with_freq(float *fft_input, int fft_size, int sample_rate) {
    printf("FFT Index | Frequency(Hz) | Magnitude\n");
    for (int i = 0; i < fft_size/2; i++) {  // only positive frequencies
        float re = fft_input[2*i];
        float im = fft_input[2*i+1];
        float mag = sqrtf(re*re + im*im);
        float freq = ((float)i * sample_rate) / fft_size;
        printf("[%4d]    %10.2f    %10.6f\n", i, freq, mag);
    }
}


void run_fft_cqt(float *buffer) {

    dsps_fft2r_init_fc32(NULL, FFT_SIZE);
    // real, img, real, img, real, ...
    for (int i = 0; i < FFT_SIZE; i++) {
        fft_input[2*i] = buffer[i];
        fft_input[2*i+1] = 0.0f;
    }

    dsps_fft2r_fc32(fft_input, FFT_SIZE);


    /*
    // Copy real input → complex buffer
    for (int i = 0; i < FFT_SIZE; i++) {
        fft_input[2*i] = buffer[i];
        fft_input[2*i+1] = 0.0f;
    }

    // Hann window
    for (int i = 0; i < FFT_SIZE; i++) {
        float w = 0.5f * (1.0f - cosf(2.0f * M_PI * i / (FFT_SIZE-1)));
        fft_input[2*i] *= w;
    }
    */

    // Magnitude
    for (int i = 0; i < FFT_SIZE/2; i++) {
        float re = fft_input[2*i];
        float im = fft_input[2*i+1];
        fft_mag[i] = sqrtf(re*re + im*im);
    }

    
    //print_fft_coeffs(fft_mag, FFT_SIZE/2);
    print_fft_with_freq(fft_input, FFT_SIZE, SAMPLE_RATE);
    /*
    // CQT binning
    for (int k = 0; k < CQT_BINS; k++) {
        float sum = 0.0f;
        int count = 0;
        for (int i = cqt_bin_start[k]; i <= cqt_bin_end[k]; i++) {
            sum += fft_mag[i];
            count++;
        }
        cqt_output[k] = (count > 0) ? sum/count : 0.0f;
    }

    // Log compression (scale to avoid saturating)
    for (int k = 0; k < CQT_BINS; k++) {
        cqt_output[k] = logf(1.0f + cqt_output[k]/1000.0f);
    }

    print_cqt();
    */
}


static bool IRAM_ATTR s_conv_done_cb(adc_continuous_handle_t handle, const adc_continuous_evt_data_t *edata, void *user_data)
{
    BaseType_t mustYield = pdFALSE;
    //Notify that ADC continuous driver has done enough number of conversions
    vTaskNotifyGiveFromISR(s_task_handle, &mustYield);

    return (mustYield == pdTRUE);
}

static void continuous_adc_init(adc_channel_t *channel, uint8_t channel_num, adc_continuous_handle_t *out_handle)
{
    adc_continuous_handle_t handle = NULL;

    adc_continuous_handle_cfg_t adc_config = {
        .max_store_buf_size = 1024,
        .conv_frame_size = READ_LEN,
    };
    ESP_ERROR_CHECK(adc_continuous_new_handle(&adc_config, &handle));

    adc_continuous_config_t dig_cfg = {
        .sample_freq_hz = 20 * 1000,
        .conv_mode = ADC_CONV_MODE,
    };

    adc_digi_pattern_config_t adc_pattern[SOC_ADC_PATT_LEN_MAX] = {0};
    dig_cfg.pattern_num = channel_num;
    for (int i = 0; i < channel_num; i++) {
        adc_pattern[i].atten = ADC_ATTEN;
        adc_pattern[i].channel = channel[i] & 0x7;
        adc_pattern[i].unit = ADC_UNIT;
        adc_pattern[i].bit_width = ADC_BIT_WIDTH;

        ESP_LOGI(TAG, "adc_pattern[%d].atten is :%"PRIx8, i, adc_pattern[i].atten);
        ESP_LOGI(TAG, "adc_pattern[%d].channel is :%"PRIx8, i, adc_pattern[i].channel);
        ESP_LOGI(TAG, "adc_pattern[%d].unit is :%"PRIx8, i, adc_pattern[i].unit);
    }
    dig_cfg.adc_pattern = adc_pattern;
    ESP_ERROR_CHECK(adc_continuous_config(handle, &dig_cfg));

    *out_handle = handle;
}

void app_main(void)
{
    esp_err_t ret;
    uint32_t ret_num = 0;
    uint8_t result[READ_LEN] = {0};
    // fill data with 0xcc to identify uninitialized reads (debug)
    memset(result, 0xcc, READ_LEN);

    s_task_handle = xTaskGetCurrentTaskHandle();

    adc_continuous_handle_t handle = NULL;
    continuous_adc_init(channel, sizeof(channel) / sizeof(adc_channel_t), &handle);

    adc_continuous_evt_cbs_t cbs = {
        .on_conv_done = s_conv_done_cb,
    };
    ESP_ERROR_CHECK(adc_continuous_register_event_callbacks(handle, &cbs, NULL));
    ESP_ERROR_CHECK(adc_continuous_start(handle));
    //init_cqt();
    while (1) {

        /**
         * This is to show you the way to use the ADC continuous mode driver event callback.
         * This `ulTaskNotifyTake` will block when the data processing in the task is fast.
         * However in this example, the data processing (print) is slow, so you barely block here.
         *
         * Without using this event callback (to notify this task), you can still just call
         * `adc_continuous_read()` here in a loop, with/without a certain block timeout.
         */
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);

        while (1) {
            // read READ_LEN bytes from the DMA buffer
            ret = adc_continuous_read(handle, result, READ_LEN, &ret_num, 0);
            if (ret == ESP_OK) {
                //ESP_LOGI("TASK", "ret is %x, ret_num is %"PRIu32" bytes", ret, ret_num);

                adc_continuous_data_t parsed_data[ret_num / SOC_ADC_DIGI_RESULT_BYTES];
                uint32_t num_parsed_samples = 0;

                esp_err_t parse_ret = adc_continuous_parse_data(handle, result, ret_num, parsed_data, &num_parsed_samples);
                if (parse_ret == ESP_OK) {
                    for (int i = 0; i < num_parsed_samples; i++) {
                        if (parsed_data[i].valid) {
                            int raw_data = parsed_data[i].raw_data & 0xFFF;
                            // normalize adc data to +-1
                            float x = (raw_data - 2048) / 2048.0f;

                            fft_buffer[fft_index++] = x;
                            
                            if (fft_index >= FFT_SIZE) {
                                // FFT buffer full → run FFT
                                run_fft_cqt(fft_buffer);
                                fft_index = 0; // reset for next block
                            }
                            //ESP_LOGI(TAG, "fft_index: %d", fft_index);
                            //ESP_LOGI(TAG, "ADC%d, Channel: %d, Value: %"PRIu32, parsed_data[i].unit + 1, parsed_data[i].channel, parsed_data[i].raw_data);
                        }
                    }
                } else {
                    ESP_LOGE(TAG, "Data parsing failed: %s", esp_err_to_name(parse_ret));
                }

                /**
                 * Because printing is slow, so every time you call `ulTaskNotifyTake`, it will immediately return.
                 * To avoid a task watchdog timeout, add a delay here. When you replace the way you process the data,
                 * usually you don't need this delay (as this task will block for a while).
                 */
                vTaskDelay(1);
            } else if (ret == ESP_ERR_TIMEOUT) {
                // DMA buffer is now empty
                break;
            }
        }
    }

    ESP_ERROR_CHECK(adc_continuous_stop(handle));
    ESP_ERROR_CHECK(adc_continuous_deinit(handle));
}

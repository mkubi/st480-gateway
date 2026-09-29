-- Notifikacie pre kotol ST-480 (dzVents skript pre Domoticz)
--
-- Posiela notifikaciu (Telegram, Pushover... podla nastavenia v Domoticzi):
--   * ked kotol prejde do alarmu (stav obsahuje "ALARM") a ked alarm skonci
--   * ked UK prekroci UK_LIMIT (znova az po poklese pod UK_RESET)
--   * ked z kotla dlhsie neprichadzaju data (gateway, prevodnik, kabel...)
--   * ked TUV klesne pod TUV_STUDENA (treba kurit) a ked sa nahreje nad
--     TUV_TEPLA (voda zohriata) - kazde len raz, kym sa stav neotoci

local STAV = 'Kotol ST-480 (Stav kotla)'
local UK = 'Kotol ST-480 (ÚK aktuálna)'
local TUV = 'Kotol ST-480 (TÚV aktuálna)'

local UK_LIMIT = 88     -- °C, upozornenie na prehrievanie
local UK_RESET = 80     -- °C, pod touto teplotou sa upozornenie znova "nabije"
local TICHO_MIN = 15    -- min bez novych dat = problem s komunikaciou

local TUV_STUDENA = 40  -- °C, pod touto teplotou: treba kurit
local TUV_TEPLA = 50    -- °C, nad touto teplotou: voda zohriata

return {
    on = {
        devices = { STAV, UK, TUV },
        timer = { 'every 5 minutes' },
    },
    data = {
        posledny_stav = { initial = '' },
        horuci = { initial = false },
        ticho = { initial = false },
        tuv = { initial = '' },     -- '' / 'studena' / 'tepla'
    },
    logging = { level = domoticz.LOG_INFO, marker = 'kotol' },

    execute = function(dz, item)
        local stav = dz.devices(STAV)
        local uk = dz.devices(UK)

        -- 1) alarmy kotla
        if item.isDevice and item.name == STAV then
            local text = stav.text or ''
            local bol = dz.data.posledny_stav
            local je_alarm = string.find(text, 'ALARM', 1, true) ~= nil
            local bol_alarm = string.find(bol, 'ALARM', 1, true) ~= nil
            if je_alarm and text ~= bol then
                dz.notify('Kotol: ALARM', text, dz.PRIORITY_HIGH)
            elseif bol_alarm and not je_alarm then
                dz.notify('Kotol', 'Alarm skončil, stav: ' .. text, dz.PRIORITY_NORMAL)
            end
            dz.data.posledny_stav = text
        end

        -- 2) prehrievanie UK
        if item.isDevice and item.name == UK then
            local t = uk.temperature
            if t >= UK_LIMIT and not dz.data.horuci then
                dz.notify('Kotol: vysoká teplota',
                    string.format('ÚK má %.1f °C!', t), dz.PRIORITY_HIGH)
                dz.data.horuci = true
            elseif t < UK_RESET then
                dz.data.horuci = false
            end
        end

        -- 3) teplota TUV
        if item.isDevice and item.name == TUV then
            local t = dz.devices(TUV).temperature
            local bol = dz.data.tuv
            if t < TUV_STUDENA and bol ~= 'studena' then
                if bol ~= '' then      -- pri prvom spusteni skriptu len zapamatat
                    dz.notify('Bojler: treba kúriť',
                        string.format('TÚV klesla na %.1f °C.', t), dz.PRIORITY_NORMAL)
                end
                dz.data.tuv = 'studena'
            elseif t > TUV_TEPLA and bol ~= 'tepla' then
                if bol ~= '' then
                    dz.notify('Bojler: voda zohriata',
                        string.format('TÚV má %.1f °C.', t), dz.PRIORITY_NORMAL)
                end
                dz.data.tuv = 'tepla'
            elseif bol == '' then
                dz.data.tuv = 'medzi'
            end
        end

        -- 4) ziadne data z kotla (gateway posiela hodnoty aspon raz za 5 min)
        if item.isTimer then
            local min = uk.lastUpdate.minutesAgo
            if min > TICHO_MIN and not dz.data.ticho then
                dz.notify('Kotol: bez spojenia',
                    'Z kotla neprišli dáta ' .. min .. ' minút.', dz.PRIORITY_NORMAL)
                dz.data.ticho = true
            elseif min <= TICHO_MIN and dz.data.ticho then
                dz.notify('Kotol', 'Spojenie s kotlom obnovené.', dz.PRIORITY_NORMAL)
                dz.data.ticho = false
            end
        end
    end
}

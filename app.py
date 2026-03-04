import streamlit as st
import geopandas as gpd
import pandas as pd
import osmnx as ox
import networkx as nx
from shapely.geometry import Polygon
import math
import tempfile
import warnings

warnings.filterwarnings('ignore')
ox.settings.timeout = 200

# Соты
def generate_hexagons(bounds, step, crs):
    xmin, ymin, xmax, ymax = bounds
    r = step / math.sqrt(3) 
    dx = step
    dy = 1.5 * r
    
    polygons = []
    row = 0
    y = ymin
    while y <= ymax + dy:
        x = xmin if row % 2 == 0 else xmin + dx / 2
        while x <= xmax + dx:
            vertices = []
            for i in range(6):
                angle = math.radians(60 * i + 30)
                vertices.append((x + r * math.cos(angle), y + r * math.sin(angle)))
            polygons.append(Polygon(vertices))
            x += dx
        y += dy
        row += 1
    return gpd.GeoDataFrame(geometry=polygons, crs=crs)

# Интерфейс
st.set_page_config(page_title="Urban Accessibility", page_icon="🏙️")

st.title("Модель интегральной доступности")
st.markdown("Этот инструмент рассчитывает демографическую нагрузку на улично-дорожную сеть. Загрузите данные по домам, и алгоритм построит гексагональную сетку с расчетом населения в изохронах.")

# Панель настроек
with st.sidebar:
    st.header("⚙️ Настройки города")
    
    # Автоматическая загрузка словаря
    DICT_PATH = "/Users/valeriapigarina/Downloads/города_россии_epsg_qgis.xlsx" 
    
    # Дежурный словарь на случай, если файл не найдется
    known_cities = {
        "Йошкар-Ола": "EPSG:32638",
        "Петропавловск-Камчатский": "EPSG:32657"
    }

    try:
        # Читаем эксель напрямую
        df_dict = pd.read_excel(DICT_PATH)
        # Убираем лишние пробелы и создаем словарь
        keys = df_dict.iloc[:, 0].astype(str).str.strip()
        vals = df_dict.iloc[:, 1].astype(str).str.strip()
        known_cities = dict(zip(keys, vals))
        st.caption(f"База городов подключена ({len(known_cities)} зап.)")
    except FileNotFoundError:
        st.warning(f"Файл {DICT_PATH} не найден рядом с кодом. Использую базовый словарь.")
    except Exception as e:
        st.error(f"Ошибка чтения словаря: {e}")

    # 2. ПОЛЯ ВВОДА
    city_name = st.text_input("Название города", value=" ")
    
    # Ищем город в загруженном словаре
    suggested_epsg = known_cities.get(city_name.strip(), " ")
    epsg_code = st.text_input("EPSG код проекции. \n Пример ввода: EPSG:32638", value=suggested_epsg)
    
    if city_name.strip() in known_cities:
        st.success(f"EPSG найден автоматически")
    else:
        st.info("Города нет в базе, введите EPSG вручную")
    
    st.markdown("---")
    grid_step = st.number_input("Шаг сетки (метры)", value=300, step=50)
    area_col = st.text_input("Колонка с площадью", value=" ")

    st.markdown("---")
    st.header("Настройки изохрон")
    
    num_isochrones = st.number_input("Количество изохрон", min_value=1, max_value=5, value=3, step=1)
    
    walk_radii = []
    for i in range(int(num_isochrones)):
        default_val = (i + 1) * 500
        rad = st.number_input(f"Радиус {i+1} (в метрах)", min_value=50, max_value=15000, value=default_val, step=100)
        walk_radii.append(rad)
        
    walk_radii = sorted(list(set(walk_radii)))

# Зона загрузки файла геоданных
uploaded_file = st.file_uploader("Загрузите файл .gpkg со слоем домов", type=['gpkg'])

# === ПАНЕЛЬ КНОПОК ===
col1, col2 = st.columns(2)

with col1:
    start_button = st.button("Запустить расчет", type="primary", use_container_width=True)

with col2:
    download_placeholder = st.empty() 

# Логика запуска
if start_button:
    if not uploaded_file:
        st.error("Пожалуйста, загрузите файл с домами!")
    else:
        with st.status("Инициализация модели...", expanded=True) as status:
            try:
                st.write("Чтение загруженного файла...")
                with tempfile.NamedTemporaryFile(delete=False, suffix=".gpkg") as tmp:
                    tmp.write(uploaded_file.getvalue())
                    tmp_path = tmp.name

                st.write("Расчет населения по нормативам...")
                houses_gdf = gpd.read_file(tmp_path)
                
                st.info(f"Прочитано точек из файла: {len(houses_gdf)}")
                
                houses_gdf[area_col] = houses_gdf[area_col].astype(str).str.replace(',', '.').astype(float)
                houses_gdf[area_col] = houses_gdf[area_col].fillna(0)
                houses_gdf['pop_calc'] = round(houses_gdf[area_col] / 20)
                
                st.info(f"Общее расчетное население во всех домах: {houses_gdf['pop_calc'].sum()} чел.")

                houses_gdf = houses_gdf.to_crs(epsg_code)

                st.write(f"Скачивание дорог для {city_name} (может занять пару минут)...")
                G = ox.graph_from_place(city_name, network_type='walk')
                G_proj = ox.project_graph(G, to_crs=epsg_code)
                city_boundary = ox.geocode_to_gdf({"city": city_name}).to_crs(epsg_code)

                st.write("Построение гексагональной сетки...")
                hex_gdf = generate_hexagons(city_boundary.total_bounds, grid_step, epsg_code)
                hex_gdf = gpd.sjoin(hex_gdf, city_boundary, predicate='intersects').drop(columns=['index_right'])
                hex_gdf['grid_id'] = range(len(hex_gdf))
                centroids = hex_gdf.geometry.centroid
                
                st.info(f"Сетка готова. Количество сот: {len(hex_gdf)}")

                st.write(f"Сетевой анализ для радиусов: {', '.join(map(str, walk_radii))} м...")
                
                for radius in walk_radii:
                    hex_gdf[f'pop_{radius}m'] = 0

                nodes, edges = ox.graph_to_gdfs(G_proj)
                grid_nodes = ox.nearest_nodes(G_proj, centroids.x, centroids.y)

                progress_bar = st.progress(0)
                total_nodes = len(grid_nodes)

                for idx, center_node in enumerate(grid_nodes):
                    for radius in walk_radii:
                        subgraph = nx.ego_graph(G_proj, center_node, radius=radius, distance='length')
                        if len(subgraph.nodes) > 2:
                            subgraph_nodes = nodes.loc[list(subgraph.nodes)]
                            isochrone_poly = subgraph_nodes.unary_union.convex_hull
                            houses_in_poly = houses_gdf[houses_gdf.geometry.intersects(isochrone_poly)]
                            hex_gdf.at[hex_gdf.index[idx], f'pop_{radius}m'] = houses_in_poly['pop_calc'].sum()
                    
                    progress_bar.progress((idx + 1) / total_nodes)

                st.write("📦 Упаковка результатов...")
                out_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".gpkg")
                out_tmp.close()
                
                export_cols = ['grid_id', 'geometry'] + [f'pop_{r}m' for r in walk_radii]
                hex_export = hex_gdf[export_cols]
                
                hex_export.to_file(out_tmp.name, layer="hexagons_polygons", driver="GPKG")
                
                pts_export = hex_export.copy()
                pts_export.geometry = centroids
                pts_export.to_file(out_tmp.name, layer="hexagons_points", driver="GPKG")

                status.update(label="✅ Расчет успешно завершен!", state="complete", expanded=False)

                with open(out_tmp.name, "rb") as f:
                    download_placeholder.download_button(
                        label="📥 Скачать результат (.gpkg)",
                        data=f.read(),
                        file_name=f"Доступность_{city_name}.gpkg",
                        mime="application/geopackage+sqlite3",
                        type="primary",
                        use_container_width=True
                    )

            except Exception as e:
                status.update(label="❌ Произошла ошибка!", state="error", expanded=True)
                st.error(f"Техническая деталь: {e}")
# Vehículos e historial en Asesor de Servicios

La ficha de servicio del vehículo se abre desde **Asesor de Servicios → Órdenes de Servicio → Vehículos e historial**, en `/dashboard/servicios/vehiculos`. El elemento del menú está inmediatamente debajo de Órdenes de Servicio.

La nueva sección permite buscar por placa, VIN, marca/modelo y cliente; pagina los vehículos y abre la ficha existente con garantías, historial, recomendaciones y Nueva ODS. Clientes y vehículos conserva el registro y edición, con tarjetas que muestran documento, contacto y vehículos, y una acción explícita para editar. El nombre del cliente y la acción Ver información del cliente abren un modal de consulta del cliente. Desde él, Editar cliente abre el formulario en un modal centrado. Los vehículos de la tarjeta no abren fichas de servicio: esas fichas permanecen en la sección del asesor.

## API y permisos

`GET /api/v1/service-orders/vehicles?filial_id=UUID&search=texto&page=1&page_size=30` devuelve una página de `{vehicle, client_name}`. Requiere VER en Asesor de Servicios para esa filial. Incluye el kilometraje actualizado y los nombres de recomendaciones, sin devolver los datos de contacto del cliente.

La lectura de historial y garantías del vehículo admite el acceso del asesor. El registro manual de garantía de fábrica admite EDITAR en Asesor de Servicios y conserva las restricciones existentes: VIN, cobertura positiva, sin garantía previa y sin venta registrada por el concesionario.

Los endpoints de administración de clientes mantienen sus permisos anteriores. No se otorga acceso a listar o modificar clientes por tener permiso de asesor. Se conservan las comprobaciones de filial en todas las operaciones.

No requiere una migración nueva. Actualizar frontend y backend juntos para disponer del nuevo endpoint.

## Verificación

TypeScript y ESLint de las vistas modificadas, 41 pruebas Node y 55 pruebas de integración PostgreSQL. La nueva prueba verifica un asesor sin permiso de Clientes: listado del módulo, búsqueda, kilometraje cero, ficha/historial y garantías; además confirma el rechazo de otra filial y de la administración de clientes.

Revisión Chromium con datos ficticios: ubicación del menú, búsqueda sin resultados y por placa, apertura de ficha, tarjetas de clientes con consulta de la información del cliente, acción explícita de edición y adaptación móvil.
